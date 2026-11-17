"""DEPARTMENT MAKEUP EXAMS (ABSENCES; requirement, 2026-10-22).

An EXAM is a term assignment with a seating chart (not a DR exam's): exams are seated, quizzes are not (MAKEUP BOOKING's own
test). A student owes a department makeup when an EXCUSED absence of theirs covers the day the exam was given to their section
(`term_given`, EXAMS "DATE GIVEN": a seating chart's date, so an exam needs no exam record yet), they are not dropped and their
paper has not come back. Read only, recomputed on every read (`rows`).

Faculty fills out the department's form (absences/makeup_form.py) for one such student only on the instructor's approval of a
Follow-up card (ASSISTANT: an Approve card, confirmation "submit"):
- From Faculty's records: the instructor's name, the course number, the student's section, last and first name (the verified
  roster's "Last, First") and UIN, the email `FORM_EMAIL`, and whether documentation is on file (any document on the absence).
- The session: the card's choice, else the first of the form's sessions after both the absence's last day and the exam's day,
  and not before tomorrow.
- The exam's rules (time allotted, Scantron, calculators, memory reset, special instructions): the card's choice, else the last
  ones given for the same exam (a request of Faculty's, else a receipt of one the instructor made on the form since three weeks
  before the exam), else the last ones given in the course term for any exam (THE TERM'S RULES, requirement, 2026-11-12), else none (the card asks). Time allotted is the exam's base time times the
  student's extra time (ACCOMMODATIONS, `makeup_length.multiplier`), rounded up: one of the form's times when it is one, else its
  Other box ("113 min"). Whether documentation is attached is the student's own (a document on the absence), never carried over.

SENT ONCE. The request row is committed before the form is posted; Google's answer is recorded once (accepted, failed, uncertain).
A failed request may be made again by a new card; an accepted or uncertain one is never sent again by Faculty (uncertain: check
the receipt in Gmail). A request ID replays its first answer.

RECEIPTS. Google emails a receipt for every response. The Inbox worker reads them from the instructor's Gmail (read only) and keeps
each once (`absence_department_receipts`, no names): one for a request of Faculty's confirms it; one Faculty did not send stands for
the instructor's own request on the form, so that student owes no card.

ONCE THE EXAM IS SEATED (requirement, 2026-11-03, of Jon Bellamy's excused absence over an Exam 3 not seated yet: the card only
came with the absence's own approval, so it never came). The Inbox worker opens a To do follow-up with the request card for a
request owed with no card, and resolves it once nothing is left to send. The card still waits for Send request (`offer_one`).

NO SITTING (requirement, 2026-10-22): a request makes no makeup sitting on Exams, so nothing reaches the calendar.

STUDENT NOTICE (requirement, 2026-10-22). Once Google accepts a
request of Faculty's, the student gets one email from the Mail page's sender (the instructor's connected TAMU Gmail), to their
roster address only: the exam, the session's day, time and room, that missing it means a 0, and to bring their student ID. Recorded
before it is sent; never sent again by itself (failed or uncertain reads from the Mail page's receipt). The worker sends one the
approval could not (Gmail away), while the session has not begun. A request the instructor made on the form itself notifies no one.

GOOGLE SIGN-IN (requirement, 2026-10-22): the form takes a response only from a signed-in TAMU Google account, so Faculty keeps a Google sign-in made in the
sign-in window (absences/google_signin.py) and sends a response only when the form was served to instructor@tamu.edu; without one
the card says so and nothing is recorded or sent.

Owner: the student's enrollment (the request and its notice), the course term (the form's link), the workspace (receipts and the
kept Google sign-in)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import time
import uuid

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import ascii_digest
from course_storage import timestamp
from exam_workflow.given_dates import term_given
from rosters import connect
from scheduling.clock import campus_today

from . import makeup_form
from .makeup_length import multiplier
from .makeups import RECEIVED
from .service import STANDING_DOCUMENT, _has, _request, dates_text, day_text, return_date, standing  # return_date: NOT BEFORE THEIR RETURN, shared

LOG = logging.getLogger(__name__)
RECEIPT_LOOKBACK = 21  # days before an exam from which a receipt of the instructor's own may stand for its makeup request
RECEIPT_EVERY = 900  # seconds between the worker's reads of Gmail for receipts while a request waits for one
RECEIPT_IDLE = 3600  # … and while none does (receipts of requests the instructor made on the form itself)
RECEIPT_TURN = 10  # receipts read in one worker turn
NOTICE_ACTOR = "department-makeup-notice"
NOTICE_IDLE = 60  # seconds between the worker's looks for a notice still to send when it found none
OFFER_IDLE = 60  # seconds between the worker's looks for a request owed with no card (ONCE THE EXAM IS SEATED)
OFFER_RETRY = 900  # … after the department's form could not be previewed for one
OFFER_SETTLE = 300  # seconds an absence's newest decision is left to its own approval's card first (AFTER THE EXCUSE)
OFFER_KEY = "department-makeup:{}:{}"  # the follow-up's request ID: one per student and exam
NOTICE_UNCERTAIN = "No answer was recorded for this email; check Gmail's Sent folder."
CARRIED = ("time", "scantron", "calculator", "reset", "instructions")  # the exam's rules one request hands to the next (never documentation)
STATES = {"owed": "Department makeup to request", "requested": "Department makeup requested", "uncertain": "Request not confirmed",
          "failed": "Request failed", "on_form": "Requested on the form"}


def _digest(value):
    return ascii_digest(value, default=str)


def split_name(name):
    """(last, first) of a roster name, kept as exported ("Last, First"); without a comma, the last word is the last name."""
    text = " ".join(str(name or "").split())
    if "," in text:
        last, _, first = text.partition(",")
        return last.strip(), first.strip()
    words = text.split(" ")
    return (words[-1], " ".join(words[:-1])) if len(words) > 1 else (text, "")


def time_choice(options, base, factor):
    """(form answer, minutes) for an exam of `base` minutes and a student's extra time: the form's own time when it is one,
    else "N min" (its Other box)."""
    minutes = math.ceil(base * factor - 1e-9)
    found = next((o for o in options if makeup_form.option_minutes(o) == minutes), None)
    return found or f"{minutes} min", minutes


class DepartmentMakeups:
    def __init__(self, absences, exams=None, gmail=None):
        self.absences, self.rosters, self.path = absences, absences.rosters, absences.path
        self.exams = exams  # ExamService: papers, read only (NO SITTING)
        self.gmail = gmail  # GmailMailService: receipts, read only
        self.followups = None  # FollowupSuggestions: ONCE THE EXAM IS SEATED; set by the app
        self._offers_at = 0.0
        self._notices_at = 0.0
        self.form = makeup_form.MakeupForm()
        self._receipts_at = 0.0  # when the worker last read Gmail for receipts (process memory)

    # -- The form's link, per course term -----------------------------------------------------------------------------------
    @staticmethod
    def _form_url(db, term_id):
        row = db.execute("SELECT * FROM absence_department_forms WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
        return (row["form_url"], row["revision"]) if row else ("", 0)

    def set_form(self, term_id, data, actor="workspace"):
        """Saves the department's form link for a course term: {request_id, form_url, revision (the one read, 0 for none)}."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = _request(data.get("request_id"))
        url = str(data.get("form_url") or "").strip()
        form = makeup_form.form_id(url)
        url = f"https://{makeup_form.HOST}/forms/d/e/{form}/viewform"
        if type(data.get("revision")) is not int:
            raise ValueError("Name the form link you read (its revision; 0 when there is none).")
        digest = _digest({"term": term_id, "url": url, "revision": data["revision"]})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM absence_department_forms WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["request_sha256"] != digest:
                    raise Conflict("This request ID already saved a different form link.")
                return {"term_id": term_id, "form_url": old["form_url"], "revision": old["revision"]}
            if not db.execute("SELECT 1 FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone():
                raise NotFound("That course term was not found.")
            current, revision = self._form_url(db, term_id)
            if data["revision"] != revision:
                raise Conflict("The form link changed since you read it. Reload it first.")
            if current == url:
                return {"term_id": term_id, "form_url": url, "revision": revision}
            db.execute("INSERT INTO absence_department_forms VALUES(?,?,?,?,?,?,?)",
                       (term_id, revision + 1, url, request_id, digest, actor, timestamp()))
            db.commit()
        return {"term_id": term_id, "form_url": url, "revision": revision + 1}

    def current_form_url(self):
        """The latest form link of the current terms ('' when none): where Faculty's Google sign-in ends (GOOGLE SIGN-IN)."""
        with connect(self.path) as db:
            if not _has(db, "absence_department_forms"):
                return ""
            row = db.execute("""SELECT f.form_url FROM absence_department_forms f JOIN course_terms t ON t.id=f.term_id
                WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY f.created_at DESC, f.rowid DESC LIMIT 1""").fetchone()
        return row[0] if row else ""

    # -- Who owes one --------------------------------------------------------------------------------------------------------
    @staticmethod
    def exams_of(db, term_id):
        """{assignment_id: title} of the term's exams: assignments a seating chart (not a DR exam's) names."""
        if not _has(db, "seating_charts", "assignments"):
            return {}
        return dict(db.execute("""SELECT DISTINCT a.id, a.title FROM seating_charts c JOIN assignments a ON a.id=c.assignment_id
            WHERE c.term_id=? AND c.dr_exam_key=''""", (term_id,)).fetchall())

    def rows(self, db, term_ids=None, members=None, absence_id=None):
        """Each exam an excused absence covers for a current student (module docstring), newest exam first, with its request state:
        {absence_id, term_id, section_id, section, enrollment_id, uin, assignment_id, title, given_date, given_time, first_date,
        last_date, absence_dates, documents, state, label, request?, receipt?}. Catalog only."""
        if not _has(db, "absences", "absence_revisions", "absence_department_requests"):
            return []
        if term_ids is None:
            term_ids = [r[0] for r in db.execute("SELECT id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY id")]
        from .makeups import _dropped, missed
        dropped = _dropped(db)
        closed = missed(db)  # MAKEUPS "MISSED": the instructor recorded the 0; no department makeup is owed any more
        found = []
        for term_id in term_ids:
            exams = self.exams_of(db, term_id)
            absences = [a for a in standing(db, term_id, members) if a["status"] == "excused" and (a["section_id"], a["enrollment_id"]) not in dropped
                        and (absence_id is None or a["id"] == absence_id)]
            if not exams or not absences:
                continue
            given = {assignment: term_given(db, term_id, assignment).get(assignment, {}) for assignment in exams}
            numbers = dict(db.execute("SELECT id, number FROM course_sections WHERE term_id=?", (term_id,)).fetchall())
            taken = set()
            for absence in absences:
                for assignment, title in exams.items():
                    day = (given[assignment].get(absence["section_id"]) or {})
                    if not day.get("date") or not absence["first_date"] <= day["date"] <= absence["last_date"]:
                        continue
                    key = (absence["enrollment_id"], assignment)
                    if key in taken or (day.get("event_id"), absence["section_id"], absence["enrollment_id"]) in closed:
                        continue
                    if self._paper_back(db, day.get("event_id"), absence["section_id"], absence["enrollment_id"]):
                        continue
                    taken.add(key)
                    found.append({"absence_id": absence["id"], "term_id": term_id, "section_id": absence["section_id"],
                                  "section": numbers.get(absence["section_id"], ""), "enrollment_id": absence["enrollment_id"],
                                  "assignment_id": assignment, "title": title, "given_date": day["date"], "given_time": day.get("time", ""),
                                  "event_id": day.get("event_id") or "", "first_date": absence["first_date"], "last_date": absence["last_date"],
                                  "absence_dates": dates_text(absence["first_date"], absence["last_date"])})
        for row in found:
            self._annotate(db, row)
        found.sort(key=lambda r: (r["given_date"], r["section"]), reverse=True)
        return found

    def _paper_back(self, db, event_id, section_id, enrollment_id):
        if not event_id or self.exams is None:
            return False
        event = db.execute("SELECT * FROM exam_events WHERE id=?", (event_id,)).fetchone()
        if not event:
            return False
        person = self.exams._participant_paper(db, dict(event), section_id, enrollment_id)
        return bool(person and person["collection"]["status"] in RECEIVED)

    def _annotate(self, db, row):
        uin = db.execute("SELECT uin FROM student_enrollments WHERE id=?", (row["enrollment_id"],)).fetchone()
        row["uin"] = uin[0] if uin else ""
        row["documents"] = db.execute(f"""SELECT COUNT(*) FROM absence_documents d WHERE d.absence_id=? AND {STANDING_DOCUMENT}""",
                                      (row["absence_id"],)).fetchone()[0] if _has(db, "absence_documents") else 0
        request = self._latest_request(db, row["enrollment_id"], row["assignment_id"])
        if request:
            row["request"] = request
            row["state"] = {"accepted": "requested", "uncertain": "uncertain", "failed": "failed"}.get(request["outcome"], "uncertain")
        else:
            receipt = self._own_receipt(db, row)
            if receipt:
                row["receipt"], row["state"] = receipt, "on_form"
            else:
                row["state"] = "owed"
        row["label"] = STATES[row["state"]]

    @staticmethod
    def _latest_request(db, enrollment_id, assignment_id):
        found = db.execute("""SELECT q.id, q.request_id, q.session, q.session_date, q.created_at, q.absence_id, o.outcome, o.detail, o.sitting_id,
                (SELECT r.gmail_message_id FROM absence_department_receipts r WHERE r.request_row_id=q.id LIMIT 1) receipt
            FROM absence_department_requests q LEFT JOIN absence_department_outcomes o ON o.request_row_id=q.id
            WHERE q.enrollment_id=? AND q.assignment_id=? ORDER BY q.created_at DESC, q.rowid DESC LIMIT 1""",
                           (enrollment_id, assignment_id)).fetchone()
        if not found:
            return None
        request = dict(found)
        request["outcome"] = request["outcome"] or "uncertain"  # recorded, never answered: perhaps sent
        request["confirmed"] = bool(request.pop("receipt"))
        request["notice"] = _notice_state(db, request["id"])
        return request

    @staticmethod
    def _own_receipt(db, row):
        """A receipt of a response the instructor made on the form itself for this student and exam: same UIN and section, a session
        on or after the exam's day, received no earlier than RECEIPT_LOOKBACK days before it."""
        if not row.get("uin"):
            return None
        since = (dt.date.fromisoformat(row["given_date"]) - dt.timedelta(days=RECEIPT_LOOKBACK)).isoformat()
        found = db.execute("""SELECT gmail_message_id, received_at, session, session_date FROM absence_department_receipts
            WHERE request_row_id IS NULL AND uin=? AND section=? AND session_date>=? AND substr(received_at,1,10)>=?
            ORDER BY received_at DESC LIMIT 1""", (row["uin"], str(row["section"]), row["given_date"], since)).fetchone()
        return dict(found) if found else None

    # -- The response, read and checked (the card's preview) ----------------------------------------------------------------
    def _row(self, db, absence_id, assignment_id):
        exists = db.execute("SELECT term_id FROM absences WHERE id=?", (str(absence_id),)).fetchone()
        if not exists:
            raise NotFound("That absence was not found.")
        if not isinstance(assignment_id, str) or not assignment_id:
            raise ValueError("Name the exam (\"assignment_id\").")
        if assignment_id not in self.exams_of(db, exists[0]):
            raise ValueError("That assignment is not an exam of this term (exams are the assignments with a seating chart).")
        absence = next((a for a in standing(db, exists[0]) if a["id"] == absence_id), None)
        rows = self.rows(db, [exists[0]], members=[(absence["section_id"], absence["enrollment_id"])]) if absence else []
        row = next((r for r in rows if r["assignment_id"] == assignment_id and r["absence_id"] == absence_id), None)
        if row is None:
            if absence and absence["status"] == "pending":
                # The same round may propose excusing it (Approve all records the decision first): the response is shown now and
                # is sent only once the absence is excused.
                pending = self._pending_row(db, exists[0], absence, assignment_id)
                if pending:
                    return pending
            raise Conflict("ABSENCES DEPARTMENT MAKEUP EXAMS: this absence does not cover that exam's day for a current student "
                           "with no paper back, or it is not excused.")
        return row

    def _pending_row(self, db, term_id, absence, assignment_id):
        day = (term_given(db, term_id, assignment_id).get(assignment_id, {}).get(absence["section_id"]) or {})
        if not day.get("date") or not absence["first_date"] <= day["date"] <= absence["last_date"]:
            return None
        row = {"absence_id": absence["id"], "term_id": term_id, "section_id": absence["section_id"],
               "section": (db.execute("SELECT number FROM course_sections WHERE id=?", (absence["section_id"],)).fetchone() or [""])[0],
               "enrollment_id": absence["enrollment_id"], "assignment_id": assignment_id, "title": self.exams_of(db, term_id)[assignment_id],
               "given_date": day["date"], "given_time": day.get("time", ""), "event_id": day.get("event_id") or "",
               "first_date": absence["first_date"], "last_date": absence["last_date"],
               "absence_dates": dates_text(absence["first_date"], absence["last_date"])}
        self._annotate(db, row)
        row["pending"] = True
        return row

    def _factor(self, db, row):
        if not _has(db, "student_accommodations"):
            return 1.0, ""
        found = db.execute("""SELECT r.snapshot_json FROM student_accommodations a JOIN accommodation_revisions r ON r.id=a.revision_id
            WHERE a.section_id=? AND a.enrollment_id=?""", (row["section_id"], row["enrollment_id"])).fetchone()
        factor = multiplier(json.loads(found[0]).get("values")) if found else 1.0
        return factor, (f"{factor:g}x extra time" if factor > 1 else "")

    def _rules(self, db, row):
        """{rule: answer} last given for this exam, else for any exam of its course term (module docstring, THE TERM'S RULES), with
        "base_minutes" for its time; {} when none."""
        for exam in (row["assignment_id"], None):
            last = db.execute("""SELECT answers_json, base_minutes FROM absence_department_requests q JOIN absence_department_outcomes o
                ON o.request_row_id=q.id WHERE q.term_id=? AND q.assignment_id=COALESCE(?, q.assignment_id) AND o.outcome='accepted'
                ORDER BY q.created_at DESC, q.rowid DESC LIMIT 1""", (row["term_id"], exam)).fetchone()
            if last:
                answers = json.loads(last[0])
                return {**{k: answers.get(k, "") for k in CARRIED}, "base_minutes": last[1]}
            found = self._receipt_rules(db, row, exam is None)
            if found:
                return found
        return {}

    def _receipt_rules(self, db, row, term):
        """The rules of the instructor's own latest receipt: for this exam (received no earlier than RECEIPT_LOOKBACK days before it,
        its session on or after the exam's day), or with `term` for any student of this course term. None when there is none."""
        member = """SELECT m.section_id, m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id
            JOIN course_sections s ON s.id=m.section_id WHERE e.uin={} AND s.number={} AND s.term_id=?"""
        if term:
            receipt = db.execute(f"""SELECT uin, section, rules_json FROM absence_department_receipts r WHERE request_row_id IS NULL
                AND EXISTS ({member.format("r.uin", "r.section")}) ORDER BY received_at DESC LIMIT 1""", (row["term_id"],)).fetchone()
        else:
            since = (dt.date.fromisoformat(row["given_date"]) - dt.timedelta(days=RECEIPT_LOOKBACK)).isoformat()
            receipt = db.execute("""SELECT uin, section, rules_json FROM absence_department_receipts WHERE request_row_id IS NULL
                AND substr(received_at,1,10)>=? AND session_date>=? ORDER BY received_at DESC LIMIT 1""", (since, row["given_date"])).fetchone()
        if not receipt:
            return None
        rules = json.loads(receipt["rules_json"])
        minutes = makeup_form.option_minutes(rules.get("time"))
        if minutes:
            # Their time for that student, back to the exam's own: divided by that student's extra time.
            other = db.execute(member.format("?", "?"), (receipt["uin"], receipt["section"], row["term_id"])).fetchone()
            factor = self._factor(db, {"section_id": other[0], "enrollment_id": other[1]})[0] if other else 1.0
            minutes = round(minutes / factor)
        return {**{k: rules.get(k, "") for k in CARRIED}, "base_minutes": minutes}

    def _names(self, row):
        names = self.absences._names([(row["section_id"], row["enrollment_id"])])
        return split_name(names.get((row["section_id"], row["enrollment_id"]), ""))

    def preview(self, absence_id, data):
        """What the response would say (writes nothing): {fingerprint, ready, missing, answers, shown, choices, state, ...}. `data`
        may name the session and any rule ("time" as the exam's base time, e.g. "75 min"; the student's extra time is applied)."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        with self.rosters.lock():
            with connect(self.path) as db:
                row = self._row(db, str(absence_id), data.get("assignment_id"))
                url, _ = self._form_url(db, row["term_id"])
                code = db.execute("SELECT code FROM course_terms WHERE id=?", (row["term_id"],)).fetchone()[0]
                factor, extra = self._factor(db, row)
                known = self._rules(db, row)
                details = (db.execute("""SELECT details FROM absence_revisions WHERE absence_id=? ORDER BY revision DESC LIMIT 1""",
                                      (row["absence_id"],)).fetchone() or [""])[0] or ""
            last, first = self._names(row)
        row = {**row, "return_date": return_date(details, row["last_date"])}
        if not url:
            raise ValueError("ABSENCES DEPARTMENT MAKEUP EXAMS: save this term's department makeup form link first "
                             "(PUT /api/absences/department-makeups/terms/<term_id>/form).")
        form = self.form.read(url)
        return self._compose(form, row, data, code, factor, extra, known, last, first)

    def _compose(self, form, row, data, code, factor, extra, known, last, first):
        questions = form["questions"]
        sessions = [(o, makeup_form.session_parts(o)) for o in questions["session"]["options"]]
        today = campus_today().isoformat()
        # NOT BEFORE THEIR RETURN (2026-10-22, Lucia Rosales Lozano: her note cleared her to return Oct 24, but her absence was
        # recorded for Oct 22 only and the card picked Fri Oct 23): never before the day the card or the absence's details say they
        # can return ("not_before", else "return(s) <Month D>" in the details).
        not_before = data.get("not_before") or row.get("return_date") or ""
        if not_before:
            try:
                not_before = dt.date.fromisoformat(str(not_before)).isoformat()
            except ValueError:
                raise ValueError('Give "not_before" as a date (YYYY-MM-DD).') from None
        after = max(row["last_date"], row["given_date"])
        open_sessions = [(o, p) for o, p in sessions if p and p["date"] > today]
        session = data.get("session")
        if session is not None and session not in questions["session"]["options"]:
            raise ValueError("Choose one of the form's makeup sessions.")
        if session is None:
            session = next((o for o, p in open_sessions if p["date"] > after and p["date"] >= not_before), "")
        parts = makeup_form.session_parts(session) if session else None
        if parts and parts["date"] <= today:
            raise ValueError("That makeup session has already passed.")
        early = bool(parts and not_before and parts["date"] < not_before)
        chosen = {k: data[k] for k in makeup_form.RULES + ("instructions",) if k in data}
        for key, value in chosen.items():
            if not isinstance(value, str):
                raise ValueError(f"Give {makeup_form.LABELS[key]} as text.")
        base = makeup_form.option_minutes(chosen["time"]) if "time" in chosen else known.get("base_minutes")
        if "time" in chosen and base is None:
            raise ValueError("Give the exam's time allotted in minutes (\"75 min\") or hours (\"2 hours\").")
        rules = {k: chosen.get(k, known.get(k, "")) for k in makeup_form.RULES + ("instructions",)}
        time_options = questions["time"]["options"]
        minutes = None
        if base:
            rules["time"], minutes = time_choice(time_options, base, factor)
        for key in ("scantron", "documentation", "calculator", "reset"):
            if rules[key] and rules[key] not in questions[key]["options"]:
                rules[key] = ""  # the form no longer offers it: asked again
        if not chosen.get("documentation"):  # the student's own: a document on their absence
            rules["documentation"] = "Yes" if row["documents"] and "Yes" in questions["documentation"]["options"] else \
                "No" if "No" in questions["documentation"]["options"] else ""
        answers = {"instructor": "Yes", "last": makeup_form.INSTRUCTOR[0], "first": makeup_form.INSTRUCTOR[1],
                   "course": code.split()[-1] if code else "", "section": str(row["section"]), "student_last": last,
                   "student_first": first, "uin": row["uin"], "session": session, **rules}
        missing = [makeup_form.LABELS[k] for k in ("session",) + makeup_form.RULES if not answers.get(k)]
        if not last or not row["uin"]:
            missing.append("the student's name and UIN")
        warnings = []
        if early:
            warnings.append(f"This session is before {day_text(not_before)}, the day they can return.")
        if form.get("signed_in") != makeup_form.FORM_EMAIL:
            # GOOGLE SIGN-IN (2026-10-22): the form takes a response only from the signed-in TAMU account.
            missing.append("Faculty's Google sign-in")
            warnings.append(makeup_form.MakeupForm.SIGN_IN)
        if row.get("pending"):
            warnings.append("The absence is still waiting for your decision; this is sent only once it is excused.")
        if row["state"] in ("requested", "uncertain"):
            warnings.append("A request for this exam was already sent: " + row["request"]["session"] + ".")
        if row["state"] == "on_form":
            warnings.append("You already requested it on the form: " + row["receipt"]["session"] + ".")
        if not open_sessions:
            warnings.append("The form lists no session after today.")
        base_text = f"{base} min" if base else ""
        choices = {"session": [o for o, p in open_sessions], "time": [o for o in time_options],
                   **{k: questions[k]["options"] for k in ("scantron", "documentation", "calculator", "reset")}}
        shown = [{"key": "student", "label": "Student", "value": f"{first} {last}".strip() + (f" · UIN {row['uin']}" if row["uin"] else "")},
                 {"key": "course", "label": "Course", "value": f"{code} · section {row['section']}"},
                 {"key": "exam", "label": "Exam", "value": f"{row['title']} (given {day_text(row['given_date'])}; absence {row['absence_dates']})"},
                 {"key": "session", "label": "Makeup session", "value": session or "Choose a session"},
                 *([{"key": "not_before", "label": "Not before", "value": f"{day_text(not_before)} (when they can return)"}] if not_before else []),
                 {"key": "time", "label": "Time allotted", "value": (rules["time"] + (f" ({base_text} × {factor:g} extra time)" if factor > 1 and base else ""))
                  if rules["time"] else "Choose the exam's time"},
                 *({"key": k, "label": makeup_form.LABELS[k], "value": rules[k] or "Choose"} for k in ("scantron", "documentation", "calculator", "reset")),
                 {"key": "instructions", "label": "Special instructions", "value": rules["instructions"] or "None"},
                 {"key": "email", "label": "Email recorded", "value": makeup_form.FORM_EMAIL}]
        fingerprint = _digest({"form": form["id"], "answers": answers, "absence": row["absence_id"], "assignment": row["assignment_id"]})
        return {"fingerprint": fingerprint, "ready": not missing, "missing": missing, "warnings": warnings, "form_title": form["title"],
                "form_url": form["url"], "email": makeup_form.FORM_EMAIL, "answers": answers, "shown": shown, "choices": choices,
                "base_time": base_text, "extra_time": extra, "state": row["state"], "absence_id": row["absence_id"],
                "assignment_id": row["assignment_id"], "title": row["title"], "enrollment_id": row["enrollment_id"],
                "section_id": row["section_id"], "session_date": (parts or {}).get("date", ""), "minutes": minutes}

    # -- Sending (the instructor's approval) ---------------------------------------------------------------------------------
    def submit(self, absence_id, data, actor="workspace"):
        """Sends one response on the instructor's approval: `data` as for `preview`, plus request_id, the preview's fingerprint and
        confirmation "submit". Returns the request with its outcome. Never sends again what was accepted or may have been."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = _request(data.get("request_id"))
        if data.get("confirmation") != "submit":
            raise ValueError("Confirm the request (\"confirmation\": \"submit\").")
        sent = {k: v for k, v in data.items() if k not in ("request_id", "confirmation", "fingerprint")}  # "attempt" names a card again
        request_hash = _digest({"absence": str(absence_id), **sent})
        with connect(self.path) as db:
            old = db.execute("SELECT * FROM absence_department_requests WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["request_sha256"] != request_hash:
                    raise Conflict("This request ID already sent a different request.")
                return self._result(db, old["id"])
        shown = self.preview(absence_id, sent)
        if data.get("fingerprint") != shown["fingerprint"]:
            raise Conflict("What this would send changed since it was shown. Look at it again.")
        if not shown["ready"]:
            if shown["missing"] == ["Faculty's Google sign-in"]:
                raise ValueError(makeup_form.MakeupForm.SIGN_IN)
            raise ValueError("Choose " + ", ".join(shown["missing"]) + " on the card first.")
        if shown["state"] in ("requested", "uncertain"):
            raise Conflict("A request for this exam was already sent (" + shown["warnings"][-1] + " Check its receipt in Gmail).")
        if shown["state"] == "on_form":
            raise Conflict("You already requested this makeup on the form.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, str(absence_id), sent.get("assignment_id"))
            if row.get("pending"):
                raise Conflict("ABSENCES DEPARTMENT MAKEUP EXAMS: the absence is not excused, so no makeup is requested.")
            latest = self._latest_request(db, row["enrollment_id"], row["assignment_id"])
            if latest and latest["outcome"] != "failed":
                raise Conflict("A request for this exam was already sent. Check its receipt in Gmail.")
            answers = shown["answers"]
            kept = {k: v for k, v in answers.items() if k not in ("student_last", "student_first")}
            row_id = str(uuid.uuid4())
            db.execute("INSERT INTO absence_department_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (row_id, request_id, request_hash, row["absence_id"], row["term_id"], row["section_id"], row["enrollment_id"],
                        row["assignment_id"], shown["form_url"], answers["session"], shown["session_date"],
                        makeup_form.option_minutes(shown["base_time"]), json.dumps(kept, sort_keys=True), _digest(answers), actor, timestamp()))
            db.commit()
        outcome, detail = "accepted", ""
        try:
            form = self.form.read(shown["form_url"])
            if _digest({"form": form["id"], "answers": answers, "absence": row["absence_id"], "assignment": row["assignment_id"]}) != shown["fingerprint"]:
                raise makeup_form.FormError("The form changed while it was being sent; nothing was sent.")
            detail = self.form.submit(form, answers)
        except makeup_form.FormError as error:
            outcome, detail = ("uncertain" if error.uncertain else "failed"), str(error)
        except Exception as error:  # noqa: BLE001 - recorded as perhaps sent; never retried by itself
            outcome, detail = "uncertain", f"Faculty stopped before Google answered ({type(error).__name__})."
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO absence_department_outcomes VALUES(?,?,?,?,?)", (row_id, outcome, str(detail)[:2000], None, timestamp()))
            db.commit()
        self._receipts_at = 0.0  # look for its receipt on the worker's next turn
        if outcome == "accepted":
            try:
                self._notify(row_id)
            except Exception:  # noqa: BLE001 - the request stands; the worker sends a notice not yet recorded, never one recorded
                LOG.exception("The department makeup notice for %s was not sent", row_id)
        with connect(self.path) as db:
            return self._result(db, row_id)

    def _result(self, db, row_id):
        found = db.execute("""SELECT q.*, o.outcome, o.detail, o.sitting_id FROM absence_department_requests q
            LEFT JOIN absence_department_outcomes o ON o.request_row_id=q.id WHERE q.id=?""", (row_id,)).fetchone()
        result = {k: found[k] for k in ("id", "request_id", "absence_id", "assignment_id", "session", "session_date", "created_at", "detail", "sitting_id")}
        result["outcome"] = found["outcome"] or "uncertain"
        result["email"] = makeup_form.FORM_EMAIL
        result["notice"] = _notice_state(db, row_id)
        if result["outcome"] == "failed":
            result["error"] = found["detail"]
        return result

    # -- The read the Assistant and the Follow-up use ------------------------------------------------------------------------
    def overview(self):
        """GET /api/absences/department-makeups: each current term's form link and each exam a student owes a department makeup of,
        with its request state; names from the verified roster."""
        with self.rosters.lock():
            with connect(self.path) as db:
                terms = [dict(r) for r in db.execute("SELECT id, code FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY code, id")]
                for term in terms:
                    term["form_url"], term["revision"] = self._form_url(db, term["id"])
                rows = self.rows(db)
            names = self.absences._names([(r["section_id"], r["enrollment_id"]) for r in rows]) if rows else {}
        for row in rows:
            row["name"] = names.get((row["section_id"], row["enrollment_id"]), "")
            row.pop("uin", None)
        return {"terms": terms, "makeups": rows, "email": makeup_form.FORM_EMAIL}

    # -- The card (ONCE THE EXAM IS SEATED) ----------------------------------------------------------------------------------------
    def offer_one(self):
        """One bounded turn: resolve a follow-up of ours with nothing left to send, else make one card (and its follow-up, the first
        time) for a request owed with none standing. Only Send request ever reaches Google's form. True when anything was saved."""
        if self.followups is None or time.monotonic() < self._offers_at + OFFER_IDLE:
            return False
        self._offers_at = time.monotonic()
        settled = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=OFFER_SETTLE)).isoformat()
        with connect(self.path) as db:
            rows = {OFFER_KEY.format(r["enrollment_id"], r["assignment_id"]): r for r in self.rows(db)}
            opened = {r[0].removeprefix("faculty:"): (r[1], r[2]) for r in db.execute("""SELECT request_id, id, item_id
                FROM student_support_suggestion_runs WHERE request_id LIKE 'faculty:department-makeup:%'""")}
            owed = [(key, r) for key, r in rows.items() if r["state"] == "owed" or r["state"] == "failed" and key in opened]
            due, standing = [], self.followups.department_cards(db) if owed else {}
            for key, row in owed:
                if self.followups.department_card_stands(standing, row["assignment_id"], f"/api/absences/{row['absence_id']}/department-makeup"):
                    continue
                newest = db.execute("SELECT MAX(created_at) FROM absence_revisions WHERE absence_id=?", (row["absence_id"],)).fetchone()[0]
                if key in opened and not self.followups.followup_open(db, opened[key][1]) or (newest or "") > settled:
                    continue
                due.append((key, row))
        for key, (_, item_id) in opened.items():
            row = rows.get(key)
            if row is None or row["state"] in ("requested", "on_form"):
                note = {"requested": "Google accepted the department's makeup request.",
                        "on_form": "The request was made on the department's form."}.get((row or {}).get("state"),
                                                                                         "No department makeup is owed any more.")
                if self.followups.resolve_faculty_followup(item_id, f"{note} (Faculty, {campus_today().isoformat()})"):
                    return True
        for key, row in due:
            return self._offer(key, row, opened.get(key))
        return False

    def _offer(self, key, row, opened):
        path = f"/api/absences/{row['absence_id']}/department-makeup"
        try:
            self.preview(row["absence_id"], {"assignment_id": row["assignment_id"]})  # refused: nothing is opened, looked at again later
            with connect(self.path) as db:
                tried = len(self.followups._actions(db, opened[0])) if opened else 0
            body = {"assignment_id": row["assignment_id"], "request_id": "$new_request_id", **({"attempt": tried + 1} if tried else {})}
            summary = f"Request the Math department's makeup of {row['title']} for this student, whose absence is excused"
            if opened:
                answer = self.followups.faculty_card(opened[0], path, body, summary)
            else:
                details = (f"This student's excused absence ({row['absence_dates']}) covers {row['title']}, given {day_text(row['given_date'])} "
                           f"to section {row['section']}, so the Math department's makeup form is owed.")
                explanation = (f"{details} Send request fills out the form from Faculty's records; nothing goes to Google before that. "
                               "Once Google accepts it, Faculty emails the student the day, time and room.")
                answer = self.followups.faculty_followup(
                    row["enrollment_id"], key, {"title": f"Request the department makeup of {row['title']}", "details": details,
                                                "next": "Check the session and the exam's rules, then Send request."},
                    explanation, path, body, summary)[2]
        except Exception:  # noqa: BLE001 - the form or the card could not be made: nothing is lost, tried again on a later turn
            LOG.exception("The department makeup card could not be made once the exam was seated")
            self._offers_at = time.monotonic() + OFFER_RETRY - OFFER_IDLE
            return False
        if (answer or {}).get("outcome") not in ("waiting_for_approval", "already_recorded"):
            LOG.warning("The department makeup card was not made: %s", (answer or {}).get("outcome"))
            self._offers_at = time.monotonic() + OFFER_RETRY - OFFER_IDLE
            return False
        return True

    # -- The student's notice (STUDENT NOTICE) --------------------------------------------------------------------------------
    @staticmethod
    def notice_message(code, title, session, address):
        """The Mail page's {to, subject, body} telling one student of their department makeup (requirement, 2026-10-22)."""
        parts = makeup_form.session_parts(session)
        day = dt.date.fromisoformat(parts["date"])
        start, end = (dt.datetime.strptime(parts[k], "%H:%M") for k in ("start", "end"))
        span = f"{start:%-I:%M} to {end:%-I:%M %p}" if f"{start:%p}" == f"{end:%p}" else f"{start:%-I:%M %p} to {end:%-I:%M %p}"
        body = "\n".join(["Hello,", "",
                          f"You are scheduled to take your {code} {title} makeup on {day:%A}, {day:%B} {day.day}, from {span} in {parts['place']}.",
                          "", f"You must take the makeup at this time. If you do not, you will receive a 0 on {title}.", "",
                          "Bring your student ID: the makeup testing room admits no one without it.", "", "Professor Sinkule"])
        return {"to": [address], "subject": f"{code} {title} makeup: {day:%a}, {day:%b} {day.day}, {span}, {parts['place']}", "body": body}

    def _notify(self, row_id):
        """Sends the student's notice of one accepted request once: preview first (nothing recorded if it fails), the notice row
        committed, then the send. A request that has a notice row is never notified again by itself. True when one was sent."""
        if self.gmail is None or not self.gmail.google.connected:
            return False
        with connect(self.path) as db:
            request = db.execute("""SELECT q.*, t.code FROM absence_department_requests q JOIN course_terms t ON t.id=q.term_id
                JOIN absence_department_outcomes o ON o.request_row_id=q.id AND o.outcome='accepted' WHERE q.id=?""", (row_id,)).fetchone()
            if not request or db.execute("SELECT 1 FROM absence_department_notices WHERE request_row_id=?", (row_id,)).fetchone():
                return False
            title = (db.execute("SELECT title FROM assignments WHERE id=?", (request["assignment_id"],)).fetchone() or ["the exam"])[0]
        person = next((p for p in self.gmail.roster_people() if (p.get("section_id"), p.get("enrollment_id"))
                       == (request["section_id"], request["enrollment_id"])), None)
        if not person or person.get("dropped") or not person.get("address"):
            return False
        data = self.notice_message(request["code"], title, request["session"], person["address"])
        preview = self.gmail.preview(data)
        gmail_request = f"department-makeup-notice:{uuid.uuid4().hex}"
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM absence_department_notices WHERE request_row_id=?", (row_id,)).fetchone():
                return False
            db.execute("INSERT INTO absence_department_notices VALUES(?,?,?)", (row_id, gmail_request, timestamp()))
            db.commit()
        try:
            self.gmail.send({**data, "request_id": gmail_request, "fingerprint": preview["fingerprint"], "confirmation": "send"}, actor=NOTICE_ACTOR)
        except Exception:  # noqa: BLE001 - recorded; it reads failed or uncertain from the Mail page's own receipt, never resent
            LOG.exception("Department makeup notice %s was not confirmed sent", gmail_request)
        return True

    def _notify_due(self):
        """One accepted request of the last two weeks whose session has not begun and that has no notice yet: notified."""
        with connect(self.path) as db:
            if not _has(db, "absence_department_notices"):
                return False
            rows = [r[0] for r in db.execute("""SELECT q.id FROM absence_department_requests q JOIN absence_department_outcomes o
                ON o.request_row_id=q.id AND o.outcome='accepted' WHERE q.session_date>? AND q.created_at>=?
                AND NOT EXISTS(SELECT 1 FROM absence_department_notices n WHERE n.request_row_id=q.id) ORDER BY q.created_at, q.rowid""",
                                            (campus_today().isoformat(), (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat()))]
        return any(self._notify(row_id) for row_id in rows[:1])

    # -- The worker's turn, and the receipts (the Inbox worker) --------------------------------------------------------------
    def process_one(self):
        """One bounded turn: a card still to make (ONCE THE EXAM IS SEATED), else a student's notice still to send (STUDENT NOTICE),
        else Gmail read for new form receipts, each kept once. True when one was made, sent or kept."""
        try:
            if self.offer_one():
                return True
        except Exception:  # noqa: BLE001 - never blocks the notices and receipts: looked at again on a later turn
            LOG.exception("Department makeup card turn failed")
        if self.gmail is None:
            return False
        if time.monotonic() >= self._notices_at + NOTICE_IDLE:
            try:
                if self._notify_due():
                    return True
            except Exception:  # noqa: BLE001 - a notice that could not be built is looked at again on a later turn
                LOG.exception("Department makeup notice turn failed")
            self._notices_at = time.monotonic()
        if not self.gmail.ready():
            return False
        with connect(self.path) as db:
            if not _has(db, "absence_department_receipts") or not db.execute("SELECT 1 FROM absence_department_forms LIMIT 1").fetchone():
                return False
            waiting = db.execute("""SELECT 1 FROM absence_department_requests q JOIN absence_department_outcomes o ON o.request_row_id=q.id
                WHERE o.outcome IN ('accepted','uncertain') AND NOT EXISTS(SELECT 1 FROM absence_department_receipts r WHERE r.request_row_id=q.id)
                AND q.created_at>=? LIMIT 1""", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat(),)).fetchone()
        if time.monotonic() < self._receipts_at + (RECEIPT_EVERY if waiting else RECEIPT_IDLE):
            return False
        self._receipts_at = time.monotonic()
        try:
            return self._read_receipts()
        except Exception:  # noqa: BLE001 - Gmail unavailable or a receipt that will not read: tried again on a later turn
            LOG.exception("Department makeup receipts could not be read from Gmail")
            return False

    def _read_receipts(self):
        threads, _ = self.gmail.client.threads(f'from:{makeup_form.RECEIPT_SENDER} subject:"Makeup Exam Request" newer_than:120d', limit=20)
        with connect(self.path) as db:
            known = {r[0] for r in db.execute("SELECT gmail_message_id FROM absence_department_receipts")}
        kept = 0
        for thread in threads:
            ids = [m.get("id") for m in thread.get("messages") or [] if isinstance(m, dict)]
            if all(i in known for i in ids if i):
                continue
            full = self.gmail.client.thread(thread["id"])
            for message in full.get("messages") or []:
                if message.get("id") in known or kept >= RECEIPT_TURN:
                    continue
                kept += self._keep_receipt(message)
                known.add(message.get("id"))
        return kept > 0

    def _keep_receipt(self, message):
        from gmail_mail.message import _decode, instant
        markup = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("mimeType") == "text/html" and (node.get("body") or {}).get("data"):
                    markup.append(_decode(node["body"]["data"]))
                for part in node.get("parts") or []:
                    walk(part)
        walk(message.get("payload") or {})
        found = makeup_form.receipt_fields(makeup_form.receipt_answers("".join(markup)))
        uin = re.sub(r"\D", "", found.get("uin", ""))
        session = found.get("session", "")
        parts = makeup_form.session_parts(session)
        if not found or len(uin) != 9 or not parts:
            return 0
        received = instant(message.get("internalDate"))
        rules = {k: found.get(k, "") for k in makeup_form.RULES + ("instructions",)}
        section = re.sub(r"\D", "", found.get("section", ""))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute("""SELECT q.id FROM absence_department_requests q JOIN course_sections s ON s.id=q.section_id
                JOIN student_enrollments e ON e.id=q.enrollment_id WHERE e.uin=? AND s.number=? AND q.session=? AND q.created_at<=?
                AND NOT EXISTS(SELECT 1 FROM absence_department_outcomes o WHERE o.request_row_id=q.id AND o.outcome='failed')
                AND NOT EXISTS(SELECT 1 FROM absence_department_receipts r WHERE r.request_row_id=q.id) ORDER BY q.created_at LIMIT 1""",
                                 (uin, section, session, _later(received))).fetchone()
            db.execute("INSERT OR IGNORE INTO absence_department_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (message["id"], received, uin, section, session, parts["date"], json.dumps(rules, sort_keys=True),
                        found.get("email", "")[:200], request[0] if request else None, timestamp()))
            db.commit()
        return 1


def _later(instant_text):
    """A request is recorded a moment before Google's receipt; allow a minute either way for clocks."""
    try:
        moment = dt.datetime.fromisoformat(str(instant_text).replace("Z", "+00:00"))
    except ValueError:
        return "9999"
    return (moment + dt.timedelta(minutes=1)).isoformat()


def _notice_state(db, row_id):
    """The student's notice of one request: None before one is recorded, else {status (accepted, failed, uncertain), detail}, the
    Mail page's own receipt of its send."""
    if not _has(db, "absence_department_notices"):
        return None
    notice = db.execute("SELECT gmail_request_id FROM absence_department_notices WHERE request_row_id=?", (row_id,)).fetchone()
    if not notice:
        return None
    found = db.execute("""SELECT o.status, o.detail FROM gmail_mail_sends s LEFT JOIN gmail_mail_outcomes o ON o.id=(SELECT MAX(x.id)
        FROM gmail_mail_outcomes x WHERE x.send_id=s.id) WHERE s.request_id=?""", (notice[0],)).fetchone() if _has(db, "gmail_mail_sends") else None
    if found and found[0] in ("accepted", "failed"):
        return {"status": found[0], "detail": "" if found[0] == "accepted" else found[1] or ""}
    return {"status": "uncertain", "detail": NOTICE_UNCERTAIN}
