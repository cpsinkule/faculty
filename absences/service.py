"""Excused absences (requirement, 2026-09-30; AGENTS.md "Excused absences"): the absences students ask to have excused, their
documentation and the instructor's decision.

Owner: the student's permanent SECTION MEMBERSHIP `(section_id, enrollment_id)` of that section's term (`absences.term_id`, held to
the section's term by a trigger). An absence's facts (dates, reason, details) and the instructor's decision are immutable
revisions (`absence_revisions`, the highest counts, each save naming the revision it was made on: 409 when stale). Documentation
is either an uploaded file kept in the SECTION's verified storage (`absences/<absence id>/<document id>.<ext>`, published immutably
under the roster lifecycle and owner locks, read back against its checksum) or a REFERENCE to an attachment already kept in the
student's communications record (the message must be linked to that student; read through the record, checked against the
checksum it had when it was picked). Documents are removed and restored by record, never deleted.

An absence touches the gradebook in ONE way (requirement, 2026-10-01): every save of an absence lets the gradebook bring that student's iClicker cells in
line with their excused absences (`GradebookService.credit_excused`, gradebook/excused_credit.py) in the same transaction, so the
revision and what it changed commit together. No other column and no exam record is ever changed (NO-GRADEBOOK otherwise stands),
and there is no step that excuses other work. Nothing here sends anything, grades anything or changes a roster. Reads of the records
are catalog-only except student names, read from the verified term roster payload under the roster lifecycle lock. Lock order
everywhere: the lifecycle lock, then the section's owner lock (files) or a payload read, then ONE `BEGIN IMMEDIATE`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from document_identity.storage import publish, read_verified
from request_ids import valid_request_id as _request
from rosters import connect
from scheduling.clock import CAMPUS_ZONE, business_days_after, day_label
from seating import student_name, term_label

REASONS = {
    "illness": "Illness",
    "family": "Death or major illness in the immediate family",
    "dependent": "Illness of a dependent family member",
    "activity": "University-authorized activity",
    "religious": "Religious holy day",
    "legal": "Legal or administrative proceeding",
    "military": "Military duty",
    "interview": "Graduate or professional school interview",
    "pregnancy": "Pregnancy or a related condition",
    "other": "Other",
}
STATUSES = {"pending": "Waiting for your decision", "excused": "Excused", "not_excused": "Not excused", "withdrawn": "Withdrawn"}
MAX_DOCUMENT = 20 * 1024 * 1024
MAX_SPAN = 120  # days one absence may cover
NOTICE_BY = 2  # RULE 7 NOTICE: business days after an absence's last day by which a notice that could not come first must come
DOCUMENT_BY = 3  # DECISION DEADLINE: Rule 7's business days for documentation, after which an undecided absence is past its deadline
NOTICES = {"advance": "Notice in advance", "after": "Notice after it began", "late": "Late notice", "none": "No notice on record"}
EXPIRY_CHECK = 60  # seconds between the worker's looks for absences past their deadline when it found none
AUTOMATIC = "automatic"  # the actor of a revision the worker records (DECISION DEADLINE)
# DECISION DEADLINE: a document stands when the latest event of it (`d`) is not a removal.
STANDING_DOCUMENT = "COALESCE((SELECT e.action FROM absence_document_events e WHERE e.document_id=d.id ORDER BY e.created_at DESC, e.rowid DESC LIMIT 1), '') != 'removed'"
IDENTIFIER = re.compile(r"[0-9a-f-]{36}")
MESSAGE_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# What an uploaded document may be, told from its first bytes (never from its name): (content type, extension).
SIGNATURES = ((b"%PDF-", ("application/pdf", "pdf")), (b"\xff\xd8\xff", ("image/jpeg", "jpg")), (b"\x89PNG\r\n\x1a\n", ("image/png", "png")))


def document_kind(content):
    """(content type, extension) of an uploaded document, or ValueError: PDF, JPEG, PNG, HEIC or WebP only."""
    for signature, kind in SIGNATURES:
        if content.startswith(signature):
            return kind
    if content[4:8] == b"ftyp" and content[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
        return "image/heic", "heic"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ValueError("Upload the documentation as a PDF or a photo (JPEG, PNG, HEIC or WebP).")


def file_name(text, fallback="document"):
    plain = unicodedata.normalize("NFKC", str(text or ""))
    plain = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", plain)
    return " ".join(plain.split()).strip(" .")[:150] or fallback


def _text(value, label, limit):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.replace("\r\n", "\n").strip()
    if CONTROL.search(value):
        raise ValueError(f"{label} holds a character that cannot be kept.")
    if len(value) > limit:
        raise ValueError(f"{label} is longer than {limit} characters.")
    return value


def _date(value, label):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ValueError(f"Give the {label} as a date.")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"The {label} is not a real date.") from None
    return value


def _attachment_ref(message_id, index):
    """ValueError unless (message ID, attachment index) is shaped like an attachment of one of the student's messages."""
    if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id) or type(index) is not int or index < 0:
        raise ValueError("Choose an attachment of one of the student's messages.")


def _facts(data, current=None):
    """The revision's facts as stored, from what the page sent (each field defaulting to the current revision's)."""
    current = current or {}
    first = _date(data.get("first_date", current.get("first_date")), "first day of the absence")
    last = _date(data.get("last_date", current.get("last_date")) or first, "last day of the absence")
    if last < first:
        raise ValueError("The last day of the absence comes before its first day.")
    if (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days >= MAX_SPAN:
        raise ValueError(f"One absence covers at most {MAX_SPAN} days; record a longer one as several.")
    reason = data.get("reason", current.get("reason"))
    if reason not in REASONS:
        raise ValueError("Choose the reason for the absence.")
    status = data.get("status", current.get("status", "pending"))
    if status not in STATUSES:
        raise ValueError("Choose a decision: waiting, excused, not excused or withdrawn.")
    # RULE 7 NOTICE: the student's own message that first told the instructor about the absence ("" or null: none on record);
    # checked against their communications record by the caller (`_check_notice`).
    notice = data.get("notice_message_id", current.get("notice_message_id")) or None
    if notice is not None and (not isinstance(notice, str) or not MESSAGE_ID.fullmatch(notice)):
        raise ValueError("Choose the student's message that told you about the absence.")
    # DEATHS WAIT: an absence for a death is never marked not excused by the worker (whatever its reason).
    death = data.get("death", current.get("death", 0))
    if type(death) not in (bool, int) or death not in (0, 1):
        raise ValueError("Say whether the absence is for a death (true or false).")
    return {"first_date": first, "last_date": last, "reason": reason,
            "details": _text(data.get("details", current.get("details", "")), "The details", 2000),
            "status": status, "decision_note": _text(data.get("decision_note", current.get("decision_note", "")), "The note on your decision", 2000),
            "notice_message_id": notice, "death": int(death)}


def notice_due(last_date):
    """RULE 7 NOTICE (Student Rule 7.1.1): the last day a notice may come when it could not come before the absence, the second
    business day after `last_date` (university holidays skipped: scheduling.clock BUSINESS DAYS)."""
    return business_days_after(dt.date.fromisoformat(last_date), NOTICE_BY).isoformat()


def documentation_due(last_date):
    """Student Rule 7.3.1.1: the last day for documentation, the third business day after `last_date` (holidays skipped)."""
    return business_days_after(dt.date.fromisoformat(last_date), DOCUMENT_BY).isoformat()


def decision_deadline(last_date):
    """DECISION DEADLINE (requirement, 2026-10-04; the third business day since 2026-10-05, "fix ... the too strict documentation
    deadline"): the campus day after `documentation_due`. From that day an absence still waiting for a decision is past its deadline
    (one for a death waits for the instructor: DEATHS WAIT)."""
    return (dt.date.fromisoformat(documentation_due(last_date)) + dt.timedelta(days=1)).isoformat()


def notice_timing(first_date, last_date, sent_at):
    """RULE 7 NOTICE (requirement, 2026-10-05): when the notice came, on the campus wall
    clock, against Rule 7.1.1: "advance" before the absence's first day, "after" from then to the end of `notice_due` (on time only
    when notice before was not possible, which the student should explain), "late" after it, "none" without a notice on record."""
    due = notice_due(last_date)
    if not sent_at:
        return {"timing": "none", "label": NOTICES["none"], "due": due}
    moment = dt.datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
    moment = (moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)).astimezone(CAMPUS_ZONE)
    day = moment.date().isoformat()
    timing = "advance" if day < first_date else "after" if day <= due else "late"
    return {"timing": timing, "label": NOTICES[timing], "due": due, "day": day,
            "at": moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")}


def _check_notice(db, notice, enrollment_id):
    """A notice is a message in this student's communications record that the student wrote (ValueError otherwise)."""
    if notice is None:
        return
    row = db.execute("""SELECT r.author_role FROM comm_links l JOIN comm_revisions r ON r.message_id=l.message_id
        AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=l.message_id)
        WHERE l.message_id=? AND l.enrollment_id=? LIMIT 1""", (notice, str(enrollment_id))).fetchone() if _has(db, "comm_links", "comm_revisions") else None
    if not row:
        raise ValueError("That message is not in this student's communications record.")
    if row["author_role"] != "student":
        raise ValueError("The notice is a message the student wrote; choose one of theirs.")


def day_text(value):
    return day_label(dt.date.fromisoformat(value))


def dates_text(first, last):
    """'Thu, Sep 17' or 'Thu, Sep 17 – Fri, Sep 18' (campus wall-clock dates, never converted)."""
    return day_text(first) if first == last else f"{day_text(first)} – {day_text(last)}"


MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"), 1)}
# 45, not 30, since 2026-10-30: clinic notes say "return to school without restrictions November 2" (37 characters between).
RETURN = re.compile(r"\b(?:return(?:s|ing)?|cleared to return|back)\b[^.;]{0,45}?\b(?:on\s+)?(?:[a-z]+day,?\s+)?"
                    r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})\b", re.I)


def return_date(details, last_date):
    """The day an absence's details say the student returns ("return October 24"), when it is after the absence's last day; ''.
    Read by the department makeup request (DEPARTMENT MAKEUP EXAMS, NOT BEFORE THEIR RETURN, 2026-10-22) and the quiz makeup
    booking card's times (MAKEUP BOOKING › FROM THEIR RETURN, 2026-10-30)."""
    found = RETURN.search(str(details or ""))
    if not found or not last_date:
        return ""
    year = int(last_date[:4])
    try:
        day = dt.date(year, MONTHS[found.group(1).lower()], int(found.group(2)))
    except ValueError:
        return ""
    if (dt.date.fromisoformat(last_date) - day).days > 180:  # a December absence returning in January
        day = day.replace(year=year + 1)
    return day.isoformat() if day.isoformat() > last_date else ""

def return_dates(db, absence_ids):
    """{absence ID: the day its latest revision's details say the student returns (`return_date`)} of the absences named that
    have one. Catalog only."""
    ids = sorted({str(a) for a in absence_ids if a})
    if not ids or not _has(db, "absence_revisions"):
        return {}
    found = {}
    for row in db.execute(f"""SELECT r.absence_id, r.details, r.last_date FROM absence_revisions r WHERE r.absence_id IN
            ({",".join("?" * len(ids))}) AND r.revision=(SELECT MAX(revision) FROM absence_revisions WHERE absence_id=r.absence_id)""", ids):
        day = return_date(row["details"], row["last_date"])
        if day:
            found[row["absence_id"]] = day
    return found


class AbsenceService:
    def __init__(self, rosters, documents, communications=None, gradebook=None, exams=None):
        self.rosters, self.path = rosters, rosters.path
        self.exams = exams  # ExamService: the papers that clear a makeup owed (MAKEUPS), read only
        self.files = documents.files  # DocumentFiles: the section's verified storage
        self.communications = communications
        self.gradebook = gradebook  # GradebookService: the iClicker credit of excused absences (ICLICKER-CREDIT)
        self.department = None  # DepartmentMakeups (department_makeups.py): DEPARTMENT MAKEUP EXAMS; set by the app
        self.booking = None  # MakeupBooking (makeup_booking.py): quiz makeups booked by card (MAKEUP BOOKING); set by the app
        self._expiry_checked = 0.0  # DECISION DEADLINE: when the worker last found nothing past its deadline (process memory)
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "absences", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _member(db, section_id, enrollment_id):
        row = db.execute("""SELECT m.section_id, m.enrollment_id, s.number, s.term_id, t.code, t.name course_name, t.term, t.term_other,
                                   t.year, t.is_current, t.deleted_at, (d.enrollment_id IS NOT NULL) dropped
                            FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id JOIN course_terms t ON t.id=s.term_id
                            LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id
                            WHERE m.section_id=? AND m.enrollment_id=?""", (str(section_id), str(enrollment_id))).fetchone()
        if not row or row["deleted_at"]:
            raise NotFound("That student is not on the section's roster.")
        return dict(row)

    @staticmethod
    def _absence(db, absence_id):
        if not isinstance(absence_id, str) or not IDENTIFIER.fullmatch(absence_id):
            raise NotFound("That absence was not found.")
        row = db.execute("SELECT * FROM absences WHERE id=?", (absence_id,)).fetchone()
        if not row:
            raise NotFound("That absence was not found.")
        return dict(row)

    @staticmethod
    def _latest(db, absence_id):
        return dict(db.execute("SELECT * FROM absence_revisions WHERE absence_id=? ORDER BY revision DESC LIMIT 1", (absence_id,)).fetchone())

    def _names(self, pairs):
        """{(section_id, enrollment_id): roster name} for the memberships named, read from each term's VERIFIED roster payload.
        The caller holds the roster lifecycle lock and no catalog transaction."""
        by_term = {}
        with connect(self.path) as db:
            for section_id, enrollment_id in pairs:
                term = db.execute("""SELECT t.* FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE s.id=?""", (section_id,)).fetchone()
                if term:
                    by_term.setdefault(term["id"], (dict(term), []))[1].append((section_id, enrollment_id))
        names = {}
        for term, members in by_term.values():
            with self.rosters.files(term) as root:
                payload = self.rosters.read_payload(root, term["roster_initialized"])
            for section_id, enrollment_id in members:
                record = payload["sections"].get(section_id, {}).get("students", {}).get(enrollment_id, {})
                names[(section_id, enrollment_id)] = student_name({**record, "id": enrollment_id})
        return names

    def _summaries(self, db, where="", params=()):
        """Each absence the query names with its latest revision and its count of documents (no names)."""
        from exam_workflow.overview import campus_today
        today = campus_today()
        # RULE 7 NOTICE: when the notice message was sent, from the communications record (its latest revision).
        sent = ("(SELECT c.sent_at FROM comm_revisions c WHERE c.message_id=r.notice_message_id ORDER BY c.revision DESC LIMIT 1)"
                if _has(db, "comm_revisions") else "NULL")
        rows = db.execute(f"""SELECT a.*, r.revision, r.first_date, r.last_date, r.reason, r.details, r.status, r.decision_note,
                   r.notice_message_id, {sent} notice_sent_at, r.death,
                   r.created_at updated_at, s.number section, t.code, t.term, t.term_other, t.year, t.is_current
            FROM absences a JOIN absence_revisions r ON r.absence_id=a.id
                 AND r.revision=(SELECT MAX(revision) FROM absence_revisions WHERE absence_id=a.id)
            JOIN course_sections s ON s.id=a.section_id JOIN course_terms t ON t.id=a.term_id
            WHERE t.deleted_at IS NULL {where} ORDER BY r.first_date DESC, a.created_at DESC, a.id""", params).fetchall()
        # Documents that stand: the latest event of each (if any) is not a removal.
        documents = {row["absence_id"]: row["n"] for row in db.execute(f"""SELECT d.absence_id, COUNT(*) n FROM absence_documents d
            WHERE {STANDING_DOCUMENT}
            GROUP BY d.absence_id""")}
        result = []
        for row in rows:
            result.append({"id": row["id"], "term_id": row["term_id"], "section_id": row["section_id"], "enrollment_id": row["enrollment_id"],
                           "section": row["section"], "code": row["code"], "term_label": term_label(row), "is_current": bool(row["is_current"]),
                           "revision": row["revision"], "first_date": row["first_date"], "last_date": row["last_date"],
                           "dates": dates_text(row["first_date"], row["last_date"]), "reason": row["reason"], "reason_label": REASONS[row["reason"]],
                           "details": row["details"], "status": row["status"], "status_label": STATUSES[row["status"]],
                           "decision_note": row["decision_note"], "recorded_at": row["created_at"], "updated_at": row["updated_at"],
                           "documents": documents.get(row["id"], 0),
                           "notice_message_id": row["notice_message_id"] or "",
                           "notice": notice_timing(row["first_date"], row["last_date"], row["notice_sent_at"] if row["notice_message_id"] else None),
                           "documentation_due": documentation_due(row["last_date"]), "death": bool(row["death"]),
                           # DECISION DEADLINE: documented and still undecided after the third business day (the page marks it).
                           "past_deadline": bool(row["status"] == "pending" and row["is_current"] and documents.get(row["id"], 0)
                                                 and decision_deadline(row["last_date"]) <= today)})
        return result

    def overview(self, scope="current"):
        """Every absence of current terms (`scope` "all": of every term), newest first, with each student's name; the terms and
        courses to filter by. Names come from the verified roster payloads."""
        with self.rosters.lock():
            with connect(self.path) as db:
                items = self._summaries(db, "" if scope == "all" else "AND t.is_current=1")
                terms = [{"id": row["id"], "code": row["code"], "term_label": term_label(row), "is_current": bool(row["is_current"])}
                         for row in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL ORDER BY is_current DESC, year DESC, term, code")]
            names = self._names({(item["section_id"], item["enrollment_id"]) for item in items})
        for item in items:
            item["name"] = names.get((item["section_id"], item["enrollment_id"]), "")
        return {"absences": items, "terms": terms, "reasons": REASONS, "statuses": STATUSES, "scope": "all" if scope == "all" else "current",
                "counts": {status: sum(1 for item in items if item["status"] == status) for status in STATUSES}}

    def students(self):
        """The current terms' students to record an absence for: names and sections (never a UIN), grouped by course and term."""
        with self.rosters.lock():
            with connect(self.path) as db:
                terms = [dict(row) for row in db.execute("SELECT * FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY code, year DESC, term")]
                members = {term["id"]: [dict(row) for row in db.execute("""SELECT m.section_id, m.enrollment_id, s.number,
                        (d.enrollment_id IS NOT NULL) dropped FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id
                        LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id
                        WHERE s.term_id=? ORDER BY s.number""", (term["id"],))] for term in terms}
            groups = []
            for term in terms:
                with self.rosters.files(term) as root:
                    payload = self.rosters.read_payload(root, term["roster_initialized"])
                students = []
                for member in members[term["id"]]:
                    record = payload["sections"].get(member["section_id"], {}).get("students", {}).get(member["enrollment_id"], {})
                    students.append({"section_id": member["section_id"], "enrollment_id": member["enrollment_id"], "section": member["number"],
                                     "name": student_name({**record, "id": member["enrollment_id"]}), "dropped": bool(member["dropped"])})
                students.sort(key=lambda student: (student["name"].casefold(), student["section"]))
                groups.append({"term_id": term["id"], "code": term["code"], "term_label": term_label(term), "students": students})
        return {"groups": groups, "reasons": REASONS, "statuses": STATUSES}

    def search(self, data):
        """Students of the current terms whose UIN is closest to the digits typed (names and sections only, never a UIN)."""
        from bubble_sheets.matching import uin_search
        typed = (data or {}).get("digits")
        if not isinstance(typed, str) or len(typed) > 20:
            raise ValueError("Send the digits typed.")
        groups = self.students()["groups"]
        with connect(self.path) as db:
            uins = {row["id"]: row["uin"] for row in db.execute("""SELECT e.id, e.uin FROM student_enrollments e
                JOIN course_terms t ON t.id=e.term_id WHERE t.is_current=1 AND t.deleted_at IS NULL""")}
        roster = [{"enrollment_id": student["enrollment_id"], "section_id": student["section_id"], "section_number": student["section"],
                   "name": student["name"], "uin": uins.get(student["enrollment_id"], "")} for group in groups for student in group["students"]]
        return {"students": uin_search(typed, [student for student in roster if student["uin"]])}

    def student(self, section_id, enrollment_id):
        """One student's absences in that section and their total of days missed (TOTAL ABSENCES, `totals.py`; catalog only; a
        card shows them)."""
        from scheduling.clock import campus_today
        from .totals import total
        with connect(self.path) as db:
            member = self._member(db, section_id, enrollment_id)
            items = self._summaries(db, "AND a.section_id=? AND a.enrollment_id=?", (member["section_id"], member["enrollment_id"]))
            missed = total(db, self.exams, member["term_id"], member["section_id"], member["enrollment_id"], today=campus_today())
        return {"section_id": member["section_id"], "enrollment_id": member["enrollment_id"], "section": member["number"],
                "absences": items, "total": missed, "reasons": REASONS, "statuses": STATUSES}

    def totals(self, scope="current"):
        """TOTAL ABSENCES › ON THE ABSENCES PAGE (requirement, 2026-10-31): every student of the current terms (`scope` "all": every
        term) with their total of days missed, grouped by section, most days first; names from the verified roster payloads."""
        from scheduling.clock import campus_today
        from .totals import total
        today, memo = campus_today(), {}
        with self.rosters.lock():
            with connect(self.path) as db:
                terms = [dict(row) for row in db.execute(f"""SELECT * FROM course_terms WHERE deleted_at IS NULL
                    {"" if scope == "all" else "AND is_current=1"} ORDER BY year DESC, term, code""")]
                sections = []
                for term in terms:
                    for section in db.execute("SELECT id, number FROM course_sections WHERE term_id=? ORDER BY number", (term["id"],)).fetchall():
                        students = [{"enrollment_id": row["enrollment_id"], "dropped": bool(row["dropped"]),
                                     **total(db, self.exams, term["id"], section["id"], row["enrollment_id"], today=today, memo=memo)}
                                    for row in db.execute("""SELECT m.enrollment_id, (d.enrollment_id IS NOT NULL) dropped FROM roster_memberships m
                                        LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id
                                        WHERE m.section_id=?""", (section["id"],))]
                        sections.append({"section_id": section["id"], "section": section["number"], "term_id": term["id"], "code": term["code"],
                                         "term_label": term_label(term), "students": students})
            names = self._names({(section["section_id"], student["enrollment_id"]) for section in sections for student in section["students"]})
        for section in sections:
            for student in section["students"]:
                student["name"] = names.get((section["section_id"], student["enrollment_id"]), "")
            section["students"].sort(key=lambda student: (-student["total"], student["name"].casefold()))
            section["total"] = sum(student["total"] for student in section["students"])
        return {"sections": sections, "scope": "all" if scope == "all" else "current", "today": today.isoformat()}

    def get(self, absence_id):
        """One absence: the student, every revision (newest first), its documents and the iClicker sessions it gives full credit
        now (`iclicker`, ICLICKER-CREDIT)."""
        with self.rosters.lock():
            with connect(self.path) as db:
                absence = self._absence(db, absence_id)
                summary = self._summaries(db, "AND a.id=?", (absence_id,))[0]
                revisions = [dict(row) for row in db.execute("SELECT * FROM absence_revisions WHERE absence_id=? ORDER BY revision DESC", (absence_id,))]
                documents = self._documents(db, absence_id)
                credit = self.gradebook.absence_credit(db, absence) if self.gradebook is not None else []
                work = self._work(db, absence)
            names = self._names({(absence["section_id"], absence["enrollment_id"])})
        for revision in revisions:
            revision.update(reason_label=REASONS[revision["reason"]], status_label=STATUSES[revision["status"]],
                            dates=dates_text(revision["first_date"], revision["last_date"]))
            for key in ("id", "absence_id", "request_id"):
                revision.pop(key, None)
        return {**summary, "name": names.get((absence["section_id"], absence["enrollment_id"]), ""), "revisions": revisions,
                "documents": documents, "iclicker": credit, "work": work, "reasons": REASONS, "statuses": STATUSES}

    def _work(self, db, absence):
        """The assignments given during this absence whose paper has not come back (MAKEUPS), each with its makeup state."""
        if self.exams is None:
            return []
        from .makeups import owed
        return owed(db, self.exams, term_ids=[absence["term_id"]], members=[(absence["section_id"], absence["enrollment_id"])],
                    absence_id=absence["id"], with_missed=True)

    def makeups(self, scope="current"):
        """Every makeup owed (MAKEUPS) in current terms (`scope` "all": every term), with each student's name; the terms to filter by.
        Names come from the verified roster payloads."""
        from .makeups import STATES, owed, seated
        if self.exams is None:
            return {"makeups": [], "states": STATES, "scope": "current", "counts": {state: 0 for state in STATES}}
        booking = {}
        with self.rosters.lock():
            with connect(self.path) as db:
                term_ids = [row[0] for row in db.execute("SELECT id FROM course_terms WHERE deleted_at IS NULL" + ("" if scope == "all" else " AND is_current=1"))]
                rows = owed(db, self.exams, term_ids=term_ids)
                terms = {row["id"]: {"code": row["code"], "term_label": term_label(row)} for row in db.execute("SELECT * FROM course_terms WHERE id IN (%s)" % ",".join("?" * len(term_ids)), term_ids)} if term_ids else {}
                if self.booking is not None:
                    # MAKEUP BOOKING: each quiz makeup's card and booking, each quiz's makeup version, and each term's setting.
                    settings = self.booking._settings(db, term_ids)
                    booking = {"versions": self.booking.annotate(db, rows),
                               "terms": {term: {**settings[term], **terms.get(term, {})} for term in term_ids}}
                # SCHEDULED WITHOUT AN ABSENCE: everyone else on a makeup sitting, after the booking reads (they get no card).
                rows += seated(db, self.exams, term_ids=term_ids, skip={(r["event_id"], r["section_id"], r["enrollment_id"]) for r in rows})
                rows.sort(key=lambda row: (row["title"].casefold(), row["section"], row["enrollment_id"]))
                rows.sort(key=lambda row: row["given_date"] or "", reverse=True)
            names = self._names({(row["section_id"], row["enrollment_id"]) for row in rows})
        for row in rows:
            row["name"] = names.get((row["section_id"], row["enrollment_id"]), "")
            row.update(terms.get(row["term_id"], {}))
        return {"makeups": rows, "states": STATES, "scope": "all" if scope == "all" else "current",
                "counts": {state: sum(1 for row in rows if row["state"] == state) for state in STATES}, **({"booking": booking} if booking else {})}

    def makeup_missed(self, data, actor="workspace"):
        """MAKEUPS "MISSED" (requirement, 2026-10-31): `missed` true records that
        the student missed the makeup of the exam record (`event_id`, `section_id`, `enrollment_id`), so it is owed no more and their
        empty cell counts as 0 until Certify records it; false is the Undo. Refused unless the makeup is owed now (or, for Undo,
        recorded missed). A repeated `request_id` returns what it did. Nothing else is written: no cell, no participant, no receipt."""
        from .makeups import owed, seated
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        if not isinstance(data.get("missed"), bool):
            raise ValueError("Say whether the makeup was missed.")
        missed = data["missed"]
        key = tuple(str(data.get(name) or "") for name in ("event_id", "section_id", "enrollment_id"))
        if not all(key):
            raise ValueError("Name the exam record and the student.")
        if self.exams is None:
            raise ValueError("Makeups are not available here.")
        with self.rosters.lock(), connect(self.path) as db:
            event = db.execute("SELECT id, term_id FROM exam_events WHERE id=?", (key[0],)).fetchone()
            if not event:
                raise NotFound("That exam record was not found.")
            prior = db.execute("SELECT * FROM absence_makeups_missed WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if ((prior["event_id"], prior["section_id"], prior["enrollment_id"]), bool(prior["missed"])) != (key, missed):
                    raise Conflict("This request ID already belongs to another change.")
                return self._missed_view(db, key)
            current = db.execute("""SELECT * FROM absence_makeups_missed WHERE event_id=? AND section_id=? AND enrollment_id=?
                ORDER BY revision DESC LIMIT 1""", key).fetchone()
            if missed == bool(current and current["missed"]):
                return self._missed_view(db, key)
            absence_id = current["absence_id"] if current else None
            if missed:
                rows = [r for r in owed(db, self.exams, term_ids=[event["term_id"]], members=[key[1:]]) if r["event_id"] == key[0]]
                if rows and rows[0]["state"] != "owed":
                    raise Conflict("The absence over this day waits for your decision: decide it first (not excused already gives the 0).")
                rows = rows or [r for r in seated(db, self.exams, term_ids=[event["term_id"]]) if (r["event_id"], r["section_id"], r["enrollment_id"]) == key]
                if not rows:
                    raise Conflict("This makeup is no longer owed: a paper of theirs came back, or no absence covers the day. Reload the Makeups view.")
                absence_id = rows[0]["absence_id"] or None
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO absence_makeups_missed VALUES(?,?,?,?,?,?,?,?,?)",
                       (*key, (current["revision"] if current else 0) + 1, int(missed), absence_id, request_id, actor, timestamp()))
            db.commit()
            view = self._missed_view(db, key)
        if self.booking is not None:
            # An unanswered booking card of a makeup no longer owed is withdrawn by the booking worker's next turn.
            self.booking._idle_until = 0.0
            self.booking.absences_wake()
        return view

    @staticmethod
    def _missed_view(db, key):
        row = db.execute("""SELECT revision, missed FROM absence_makeups_missed WHERE event_id=? AND section_id=? AND enrollment_id=?
            ORDER BY revision DESC LIMIT 1""", key).fetchone()
        return {"event_id": key[0], "section_id": key[1], "enrollment_id": key[2], "missed": bool(row and row["missed"]),
                "revision": row["revision"] if row else 0}

    @staticmethod
    def _documents(db, absence_id):
        found = []
        for row in db.execute("SELECT * FROM absence_documents WHERE absence_id=? ORDER BY created_at, id", (absence_id,)):
            last = db.execute("SELECT action, created_at FROM absence_document_events WHERE document_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (row["id"],)).fetchone()
            found.append({"id": row["id"], "kind": row["kind"], "name": row["name"], "content_type": row["content_type"], "size": row["size"],
                          "message_id": row["message_id"], "added_at": row["created_at"], "removed": bool(last and last["action"] == "removed"),
                          "url": f"/api/absences/{absence_id}/documents/{row['id']}"})
        return found

    # -- Recording ----------------------------------------------------------------------------------------------------------
    def _credit(self, db, absence, actor):
        """The gradebook's iClicker credit for this student's excused absences, in the caller's transaction (ICLICKER-CREDIT):
        {"filled": n, "put_back": n}, or None where there is no gradebook."""
        if self.gradebook is None:
            return None
        return self.gradebook.credit_excused(db, absence["term_id"], [(absence["section_id"], absence["enrollment_id"])], actor=actor)

    def create(self, data, actor="workspace"):
        """Records a new absence (its first revision); an excused one gives the iClicker sessions within its dates full credit in
        the same transaction (`iclicker_credit` says how many). The same request ID returns the absence it made.

        WITH DOCUMENTS (requirement, 2026-10-04, of a follow-up whose receipts could only be attached in a later round because the
        absence's ID did not exist yet): `documents`, up to 10 `{"message_id", "index"}`, names attachments already kept in this
        student's communications record; each is checked and read before anything is written and kept as a reference in the same
        transaction as the absence, as Add from their messages keeps it. One that cannot be read records nothing."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        facts = _facts(data)
        wanted = self._message_documents(data.get("documents"))
        if facts["notice_message_id"]:
            with connect(self.path) as db:
                _check_notice(db, facts["notice_message_id"], data.get("enrollment_id"))
        if wanted:  # checked and read before the transaction, as Add from their messages does (reading parses the kept email)
            with connect(self.path) as db:
                for message_id, _ in wanted:
                    if not db.execute("SELECT 1 FROM comm_links WHERE message_id=? AND enrollment_id=?", (message_id, str(data.get("enrollment_id")))).fetchone():
                        raise ValueError("That message is not in this student's communications record.")
        read = [(message_id, index, *self.communications.attachment(message_id, index)) for message_id, index in wanted]
        credit = None
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            member = self._member(db, data.get("section_id"), data.get("enrollment_id"))
            done = db.execute("SELECT id FROM absences WHERE term_id=? AND request_id=?", (member["term_id"], request_id)).fetchone()
            if done:
                existing = self._absence(db, done["id"])
                if (existing["section_id"], existing["enrollment_id"]) != (member["section_id"], member["enrollment_id"]):
                    raise Conflict("This request ID already recorded another student's absence. Close the window and record it again.")
                absence_id = done["id"]
            else:
                absence_id, when = str(uuid.uuid4()), timestamp()
                db.execute("INSERT INTO absences VALUES (?,?,?,?,?,?,?)",
                           (absence_id, member["term_id"], member["section_id"], member["enrollment_id"], request_id, actor, when))
                self._insert_revision(db, absence_id, 1, facts, request_id, actor)
                for number, (message_id, index, name, kind, content) in enumerate(read):
                    self._insert_message_document(db, absence_id, message_id, index, name, kind, content, f"{request_id[:94]}#{number}", actor)
                credit = self._credit(db, member, actor)
            db.commit()
        return self._answer(absence_id, credit)

    def _message_documents(self, documents):
        """[(message ID, attachment index)] of a create's `documents`, checked for shape; [] when none are named."""
        if documents in (None, []):
            return []
        if not isinstance(documents, list) or len(documents) > 10:
            raise ValueError("Name up to 10 attachments of the student's messages.")
        if self.communications is None:
            raise ValueError("The communications record is not available here.")
        wanted = []
        for entry in documents:
            message_id, index = (entry.get("message_id"), entry.get("index")) if isinstance(entry, dict) else (None, None)
            _attachment_ref(message_id, index)
            if (message_id, index) not in wanted:
                wanted.append((message_id, index))
        return wanted

    @staticmethod
    def _insert_message_document(db, absence_id, message_id, index, name, kind, content, request_id, actor):
        db.execute("""INSERT INTO absence_documents(id,absence_id,kind,name,content_type,size,sha256,path,message_id,attachment_index,request_id,actor,created_at)
                      VALUES (?,?,'message',?,?,?,?,NULL,?,?,?,?,?)""",
                   (str(uuid.uuid4()), absence_id, file_name(name, "attachment")[:200], str(kind or "application/octet-stream")[:100],
                    len(content), hashlib.sha256(content).hexdigest(), message_id, index, request_id, actor, timestamp()))

    def _answer(self, absence_id, credit):
        """What a save answers: the absence, with what its save changed in the gradebook when it changed anything."""
        found = self.get(absence_id)
        return {**found, "iclicker_credit": credit} if credit and any(credit.values()) else found

    @staticmethod
    def _insert_revision(db, absence_id, revision, facts, request_id, actor):
        db.execute("""INSERT INTO absence_revisions(id,absence_id,revision,first_date,last_date,reason,details,status,decision_note,request_id,actor,created_at,
                          notice_message_id,death) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (str(uuid.uuid4()), absence_id, revision, facts["first_date"], facts["last_date"], facts["reason"], facts["details"],
                    facts["status"], facts["decision_note"], request_id, actor, timestamp(), facts.get("notice_message_id"), facts.get("death", 0)))

    def revise(self, absence_id, data, actor="workspace"):
        """Records the absence's next revision (its dates, reason, details or your decision). `base_revision` names the revision the
        page showed (409 when it changed); an identical revision records nothing. A new revision brings the student's iClicker
        credit in line with their excused absences in the same transaction (ICLICKER-CREDIT; `iclicker_credit` says what changed)."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        base = data.get("base_revision")
        if type(base) is not int or base < 1:
            raise ValueError("Name the revision the page showed as base_revision.")
        credit = None
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            absence = self._absence(db, absence_id)
            # A repeated request returns what it did (the view is read after this transaction: it reads roster names).
            if not db.execute("SELECT 1 FROM absence_revisions WHERE absence_id=? AND request_id=?", (absence_id, request_id)).fetchone():
                current = self._latest(db, absence_id)
                if current["revision"] != base:
                    raise Conflict("This absence was changed elsewhere. Close the window and open it again.")
                facts = _facts(data, current)
                if facts["notice_message_id"] != current["notice_message_id"]:
                    _check_notice(db, facts["notice_message_id"], absence["enrollment_id"])
                if any(facts[key] != current[key] for key in facts):
                    self._insert_revision(db, absence_id, base + 1, facts, request_id, actor)
                    credit = self._credit(db, absence, actor)
            db.commit()
        return self._answer(absence_id, credit)

    # -- Documents ----------------------------------------------------------------------------------------------------------
    def upload(self, absence_id, content, filename, request_id, actor="workspace"):
        """Keeps an uploaded document in the section's verified storage. The same request ID with the same file returns it."""
        request_id = _request(request_id)
        if not isinstance(content, bytes) or not content:
            raise ValueError("Choose the documentation file.")
        if len(content) > MAX_DOCUMENT:
            raise ValueError("That file is larger than 20 MB.")
        kind, extension = document_kind(content)
        digest = hashlib.sha256(content).hexdigest()
        name = file_name(PurePosixPath(str(filename or "").replace("\\", "/")).name, f"documentation.{extension}")[:200]
        with connect(self.path) as db:
            absence = self._absence(db, absence_id)
            done = db.execute("SELECT * FROM absence_documents WHERE absence_id=? AND request_id=?", (absence_id, request_id)).fetchone()
            if done:
                if done["sha256"] != digest:
                    raise Conflict("This request ID already added another document. Close the window and add it again.")
                return self.get(absence_id)
        document_id = str(uuid.uuid5(uuid.UUID(absence_id), f"{request_id}:{digest}"))  # a retried upload reuses its own file
        path = f"absences/{absence_id}/{document_id}.{extension}"
        with self.rosters.lock(), self.files.files("section", absence["section_id"], write=True) as root:
            # Refuse a concurrent request-ID collision before publishing an otherwise unreferenced file.
            with connect(self.path) as db:
                done = db.execute("SELECT sha256 FROM absence_documents WHERE absence_id=? AND request_id=?", (absence_id, request_id)).fetchone()
                if done and done["sha256"] != digest:
                    raise Conflict("This request ID already added another document. Close the window and add it again.")
            publish(root / path, content)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                # ABSENCES: a competing retry may have acquired the lifecycle lock after the initial read.
                done = db.execute("SELECT sha256 FROM absence_documents WHERE absence_id=? AND request_id=?", (absence_id, request_id)).fetchone()
                if done and done["sha256"] != digest:
                    raise Conflict("This request ID already added another document. Close the window and add it again.")
                if not done:
                    db.execute("""INSERT INTO absence_documents(id,absence_id,kind,name,content_type,size,sha256,path,message_id,attachment_index,request_id,actor,created_at)
                                  VALUES (?,?,'upload',?,?,?,?,?,NULL,NULL,?,?,?)""",
                               (document_id, absence_id, name, kind, len(content), digest, path, request_id, actor, timestamp()))
                db.commit()
        return self.get(absence_id)

    def attach_message(self, absence_id, data, actor="workspace"):
        """Refers to an attachment already kept in the student's communications record (the student's email or Canvas message)."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        message_id, index = data.get("message_id"), data.get("index")
        _attachment_ref(message_id, index)
        if self.communications is None:
            raise ValueError("The communications record is not available here.")
        with connect(self.path) as db:
            absence = self._absence(db, absence_id)
            if not db.execute("SELECT 1 FROM comm_links WHERE message_id=? AND enrollment_id=?", (message_id, absence["enrollment_id"])).fetchone():
                raise ValueError("That message is not in this student's communications record.")
            done = db.execute("SELECT * FROM absence_documents WHERE absence_id=? AND request_id=?", (absence_id, request_id)).fetchone()
            if done:
                if (done["message_id"], done["attachment_index"]) != (message_id, index):
                    raise Conflict("This request ID already added another document. Close the window and add it again.")
                return self.get(absence_id)
        name, kind, content = self.communications.attachment(message_id, index)  # checked against the record's own checksum
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            done = db.execute("SELECT message_id, attachment_index FROM absence_documents WHERE absence_id=? AND request_id=?", (absence_id, request_id)).fetchone()
            if done and (done["message_id"], done["attachment_index"]) != (message_id, index):
                raise Conflict("This request ID already added another document. Close the window and add it again.")
            if not done:
                self._insert_message_document(db, absence_id, message_id, index, name, kind, content, request_id, actor)
            db.commit()
        return self.get(absence_id)

    def document_event(self, absence_id, document_id, action, data, actor="workspace"):
        """Removes a document from the absence, or restores it (a record; the file and the reference stay)."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        if action not in ("removed", "restored"):
            raise ValueError("Remove or restore the document.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._absence(db, absence_id)
            if not db.execute("SELECT 1 FROM absence_documents WHERE id=? AND absence_id=?", (document_id, absence_id)).fetchone():
                raise NotFound("That document is not part of this absence.")
            if not db.execute("SELECT 1 FROM absence_document_events WHERE document_id=? AND request_id=?", (document_id, request_id)).fetchone():
                last = db.execute("SELECT action FROM absence_document_events WHERE document_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (document_id,)).fetchone()
                if (last["action"] if last else "restored") != action:
                    db.execute("INSERT INTO absence_document_events VALUES (?,?,?,?,?,?)",
                               (str(uuid.uuid4()), document_id, action, request_id, actor, timestamp()))
            db.commit()
        return self.get(absence_id)

    def read_document(self, absence_id, document_id):
        """(file name, content type, bytes) of a document, checked against the checksum it was kept with."""
        with connect(self.path) as db:
            absence = self._absence(db, absence_id)
            row = db.execute("SELECT * FROM absence_documents WHERE id=? AND absence_id=?", (document_id, absence_id)).fetchone()
        if not row:
            raise NotFound("That document is not part of this absence.")
        if row["kind"] == "message":
            name, kind, content = self.communications.attachment(row["message_id"], row["attachment_index"])
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise NotFound("The message's attachment is no longer the one that was picked.")
            return row["name"], row["content_type"], content
        with self.rosters.lock(), self.files.files("section", absence["section_id"]) as root:
            content = read_verified(root, row["path"], row["sha256"])
        return row["name"], row["content_type"], content

    # -- Attention ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def waiting(db):
        """How many absences of current terms wait for the instructor's decision (Home's Needs you and the sidebar badge)."""
        if not _has(db, "absences", "absence_revisions"):
            return 0
        return db.execute("""SELECT COUNT(*) FROM absences a JOIN course_terms t ON t.id=a.term_id
            JOIN absence_revisions r ON r.absence_id=a.id AND r.revision=(SELECT MAX(revision) FROM absence_revisions WHERE absence_id=a.id)
            WHERE t.is_current=1 AND t.deleted_at IS NULL AND r.status='pending'""").fetchone()[0]

    @staticmethod
    def _past_deadline(db, today):
        """[(absence ID, documented, death)] of the undecided absences of current terms whose decision deadline has come, leaving out
        one the worker already decided once (the instructor reopened it: theirs to decide)."""
        if not _has(db, "absences", "absence_revisions"):
            return []
        rows = db.execute(f"""SELECT a.id, r.last_date, r.death, EXISTS(SELECT 1 FROM absence_documents d WHERE d.absence_id=a.id
                AND {STANDING_DOCUMENT}) documented
            FROM absences a JOIN course_terms t ON t.id=a.term_id
            JOIN absence_revisions r ON r.absence_id=a.id AND r.revision=(SELECT MAX(revision) FROM absence_revisions WHERE absence_id=a.id)
            WHERE t.is_current=1 AND t.deleted_at IS NULL AND r.status='pending'
              AND NOT EXISTS(SELECT 1 FROM absence_revisions x WHERE x.absence_id=a.id AND x.actor=?)
            ORDER BY r.last_date, a.id""", (AUTOMATIC,)).fetchall()
        return [(row["id"], bool(row["documented"]), bool(row["death"])) for row in rows if decision_deadline(row["last_date"]) <= today]

    def past_deadline_documented(self, db):
        """How many undecided absences with documentation are past their decision deadline (Home leads its absences card with
        them; DECISION DEADLINE)."""
        from exam_workflow.overview import campus_today
        return sum(1 for _, documented, _ in self._past_deadline(db, campus_today()) if documented)

    def expire_one(self, today=None):
        """DECISION DEADLINE (requirement, 2026-10-04). One undecided absence of a
        current term with no documentation, past its deadline, gets a revision marking it not excused, recorded by the worker
        (actor "automatic") with a note saying why; iClicker credit follows as on any save. Once only: an absence the instructor
        reopens stays theirs. A bounded worker turn (teach_inbox work_once); True when it recorded one."""
        if today is None and time.monotonic() - self._expiry_checked < EXPIRY_CHECK:
            return False
        from exam_workflow.overview import campus_today
        today = today or campus_today()
        with connect(self.path) as db:
            due = [ident for ident, documented, death in self._past_deadline(db, today) if not documented and not death]
        if not due:
            self._expiry_checked = time.monotonic()
            return False
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if due[0] in [ident for ident, documented, death in self._past_deadline(db, today) if not documented and not death]:
                absence, current = self._absence(db, due[0]), self._latest(db, due[0])
                last = (dt.date.fromisoformat(decision_deadline(current["last_date"])) - dt.timedelta(days=1)).isoformat()
                note = (f"Not excused automatically: no documentation and no decision by the end of {dates_text(last, last)}, "
                        "the third business day after the absence.")
                facts = {**{key: current[key] for key in ("first_date", "last_date", "reason", "details", "notice_message_id", "death")}, "status": "not_excused",
                         "decision_note": (note + ("\n" + current["decision_note"] if current["decision_note"] else ""))[:2000]}
                self._insert_revision(db, due[0], current["revision"] + 1, facts, f"automatic-not-excused:{due[0]}:{current['revision']}", AUTOMATIC)
                self._credit(db, absence, AUTOMATIC)
            db.commit()
        return True

    def makeups_owed(self, db):
        """How many makeups of current terms are owed for excused absences (MAKEUPS; Home's Needs you and the sidebar badge). An
        undecided absence's makeup is a heads-up on the Absences page only."""
        if self.exams is None:
            return 0
        from .makeups import count_owed
        return count_owed(db, self.exams)

    def makeups_attention(self, db):
        """Home's makeups card: {"count"} of makeups owed as before, or, once a current term books quiz makeups by card (MAKEUP
        BOOKING), {"count", "parts"}: only what needs the instructor (a flag, rubrics to finalize, copies to
        print, and every makeup owed in a term whose cards are off). None when nothing does."""
        if self.exams is None:
            return None
        from .makeups import owed
        rows = owed(db, self.exams)
        parts = self.booking.attention(db, rows) if self.booking is not None else None
        if parts is None:
            count = sum(1 for row in rows if row["state"] == "owed")
            return {"count": count} if count else None
        return {"count": sum(parts.values()), "parts": parts} if any(parts.values()) else None


def standing(db, term_id, members=None):
    """Each absence of the term (only those of the named `(section_id, enrollment_id)` memberships, when given) with its latest
    revision's dates and decision: what the gradebook's iClicker credit reads (ICLICKER-CREDIT). Catalog only; [] before the
    absences tables exist."""
    if not _has(db, "absences", "absence_revisions"):
        return []
    wanted = None if members is None else {(str(section), str(enrollment)) for section, enrollment in members}
    rows = db.execute("""SELECT a.id, a.section_id, a.enrollment_id, r.status, r.first_date, r.last_date FROM absences a
        JOIN absence_revisions r ON r.absence_id=a.id AND r.revision=(SELECT MAX(revision) FROM absence_revisions WHERE absence_id=a.id)
        WHERE a.term_id=? ORDER BY r.first_date, a.id""", (term_id,)).fetchall()
    return [{**dict(row), "dates": dates_text(row["first_date"], row["last_date"])} for row in rows
            if wanted is None or (row["section_id"], row["enrollment_id"]) in wanted]


def _has(db, *tables):
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(table in names for table in tables)
