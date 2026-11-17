"""Student notes (requirement, 2026-10-01; AGENTS.md "Student notes"): the instructor's own notes about a student, such as "scheduled
one-on-one office hours today and didn't show", each with the day it is about.

Owner: the student's TERM ENROLLMENT (`student_enrollments.id`; `student_notes.term_id` is held to the enrollment's term by a
trigger), so a note stays with the student when they change sections within the term and never reaches another term or course. A
note's date and words are immutable revisions (`student_note_revisions`, the highest counts, each save naming the revision the
page showed: 409 when it changed since); deleting a note is a revision that marks it removed, and restoring it another. Nothing is
erased, sent anywhere, graded or shown on Home. Notes are catalog data: the nightly catalog backup keeps them and the generic
workspace export leaves them out.

Reads of one student's notes are catalog-only and take no lock. The list of students to choose from reads names from each current
term's verified roster payload under the roster lifecycle lock. A write takes the lifecycle lock, then ONE `BEGIN IMMEDIATE`.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from request_ids import valid_request_id as _request
from rosters import connect
from seating import student_name, term_label

MAX_TEXT = 5000
IDENTIFIER = re.compile(r"[0-9a-f-]{36}")
ENROLLMENT = re.compile(r"[1-9][0-9]{9}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value):
    if not isinstance(value, str):
        raise ValueError("Write the note.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise ValueError("Write the note.")
    if CONTROL.search(value):
        raise ValueError("The note holds a character that cannot be kept.")
    if len(value) > MAX_TEXT:
        raise ValueError(f"A note is at most {MAX_TEXT:,} characters.")
    return value


def _date(value):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ValueError("Give the note's date.")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("The note's date is not a real date.") from None
    return value


def _base(data):
    base = data.get("base_revision")
    if type(base) is not int or base < 1:
        raise ValueError("Name the revision the page showed as base_revision.")
    return base


class StudentNoteService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "student_notes", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _enrollment(db, enrollment_id):
        if not isinstance(enrollment_id, str) or not ENROLLMENT.fullmatch(enrollment_id):
            raise NotFound("That student was not found.")
        row = db.execute("""SELECT e.id, e.term_id, t.code, t.term, t.term_other, t.year, t.deleted_at FROM student_enrollments e
                            JOIN course_terms t ON t.id=e.term_id WHERE e.id=?""", (enrollment_id,)).fetchone()
        if not row or row["deleted_at"]:
            raise NotFound("That student was not found.")
        return dict(row)

    @staticmethod
    def _note(db, note_id):
        if not isinstance(note_id, str) or not IDENTIFIER.fullmatch(note_id):
            raise NotFound("That note was not found.")
        row = db.execute("SELECT * FROM student_notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise NotFound("That note was not found.")
        return dict(row)

    @staticmethod
    def _latest(db, note_id):
        return dict(db.execute("SELECT * FROM student_note_revisions WHERE note_id=? ORDER BY revision DESC LIMIT 1", (note_id,)).fetchone())

    def _view(self, db, enrollment):
        """The student's notes that stand (not deleted), the day they are about newest first."""
        rows = db.execute("""SELECT n.id, n.created_at, r.revision, r.note_date, r.body, r.created_at updated_at,
                   f.note_date first_date, f.body first_body
            FROM student_notes n
            JOIN student_note_revisions r ON r.note_id=n.id AND r.revision=(SELECT MAX(revision) FROM student_note_revisions WHERE note_id=n.id)
            JOIN student_note_revisions f ON f.note_id=n.id AND f.revision=1
            WHERE n.enrollment_id=? AND r.removed=0 ORDER BY r.note_date DESC, n.created_at DESC, n.id""", (enrollment["id"],)).fetchall()
        notes = [{"id": row["id"], "revision": row["revision"], "date": row["note_date"], "text": row["body"],
                  "recorded_at": row["created_at"], "updated_at": row["updated_at"],
                  "edited": (row["note_date"], row["body"]) != (row["first_date"], row["first_body"])} for row in rows]
        return {"enrollment_id": enrollment["id"], "term_id": enrollment["term_id"], "code": enrollment["code"] or "",
                "term_label": term_label(enrollment), "notes": notes}

    def student(self, enrollment_id):
        """One student's notes in their term (catalog only)."""
        with connect(self.path) as db:
            return self._view(db, self._enrollment(db, enrollment_id))

    def students(self):
        """The current terms' students to write a note about: names and sections (never a UIN), one entry per enrollment, grouped by
        course and term. Names come from each term's verified roster payload."""
        with self.rosters.lock():
            with connect(self.path) as db:
                terms = [dict(row) for row in db.execute("SELECT * FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY code, year DESC, term")]
                members = {term["id"]: [dict(row) for row in db.execute("""SELECT m.section_id, m.enrollment_id, s.number,
                        (d.enrollment_id IS NOT NULL) dropped FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id
                        LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id
                        WHERE s.term_id=? ORDER BY s.number""", (term["id"],))] for term in terms}
            groups = []
            for term in terms:
                if not members[term["id"]]:
                    continue
                with self.rosters.files(term) as root:
                    payload = self.rosters.read_payload(root, term["roster_initialized"])
                found = {}
                for member in members[term["id"]]:
                    record = payload["sections"].get(member["section_id"], {}).get("students", {}).get(member["enrollment_id"], {})
                    entry = found.setdefault(member["enrollment_id"], {"enrollment_id": member["enrollment_id"], "section_id": member["section_id"],
                                                                       "name": student_name({**record, "id": member["enrollment_id"]}),
                                                                       "sections": [], "dropped": True})
                    entry["sections"].append(member["number"])
                    entry["dropped"] = entry["dropped"] and bool(member["dropped"])  # dropped only when out of every section
                students = sorted(found.values(), key=lambda student: (student["name"].casefold(), student["sections"]))
                groups.append({"term_id": term["id"], "code": term["code"], "term_label": term_label(term), "students": students})
        return {"groups": groups}

    def term_names(self, term_id):
        """Every name on one term's verified roster, whether or not the term is current, for words that leave only with its
        students' names out of them (GRADING "RUBRIC MEMORIES": a learning pass over an assignment of a term that is over).
        [] for a term that is gone or never had a roster; a roster that cannot be read raises, as `students` does."""
        with self.rosters.lock():
            with connect(self.path) as db:
                term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone()
            if not term:
                return []
            term = dict(term)
            with self.rosters.files(term) as root:
                payload = self.rosters.read_payload(root, term["roster_initialized"])
        return sorted({student_name({**record, "id": ident}) for section in payload["sections"].values()
                       for ident, record in section.get("students", {}).items()})

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
        roster = [{"enrollment_id": student["enrollment_id"], "section_id": student["section_id"],
                   "section_number": ", ".join(str(number) for number in student["sections"]), "name": student["name"],
                   "uin": uins.get(student["enrollment_id"], "")} for group in groups for student in group["students"]]
        found = uin_search(typed, [student for student in roster if student["uin"]])
        return {"students": [{key: item[key] for key in ("enrollment_id", "name", "section_number", "similarity")} for item in found]}

    # -- Writing ------------------------------------------------------------------------------------------------------------
    def create(self, data, actor="workspace"):
        """Records a new note about the student (its first revision). The same request ID returns the note it made."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        date, text = _date(data.get("date")), _text(data.get("text"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            enrollment = self._enrollment(db, data.get("enrollment_id"))
            done = db.execute("SELECT id, enrollment_id FROM student_notes WHERE term_id=? AND request_id=?", (enrollment["term_id"], request_id)).fetchone()
            if done:
                if done["enrollment_id"] != enrollment["id"]:
                    raise Conflict("This request ID already recorded a note about another student. Write the note again.")
                note_id = done["id"]
            else:
                note_id = str(uuid.uuid4())
                db.execute("INSERT INTO student_notes VALUES (?,?,?,?,?,?)",
                           (note_id, enrollment["term_id"], enrollment["id"], request_id, actor, timestamp()))
                self._insert(db, note_id, 1, date, text, False, request_id, actor)
            view = self._view(db, enrollment)
            db.commit()
        return {**view, "note_id": note_id}

    @staticmethod
    def _insert(db, note_id, revision, date, text, removed, request_id, actor):
        db.execute("""INSERT INTO student_note_revisions(id,note_id,revision,note_date,body,removed,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?)""",
                   (str(uuid.uuid4()), note_id, revision, date, text, int(removed), request_id, actor, timestamp()))

    def _change(self, note_id, data, actor, change):
        """Records the note's next revision from `change(latest) -> (date, text, removed)` on the revision the page showed (409 when
        it changed since). A repeated request returns what it did; a revision identical to the latest records nothing."""
        data = data if isinstance(data, dict) else {}
        request_id, base = _request(data.get("request_id")), _base(data)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            note = self._note(db, note_id)
            enrollment = self._enrollment(db, note["enrollment_id"])
            if not db.execute("SELECT 1 FROM student_note_revisions WHERE note_id=? AND request_id=?", (note_id, request_id)).fetchone():
                current = self._latest(db, note_id)
                if current["revision"] != base:
                    raise Conflict("This note was changed elsewhere. Close the student's window and open it again.")
                date, text, removed = change(current)
                if (date, text, int(removed)) != (current["note_date"], current["body"], current["removed"]):
                    self._insert(db, note_id, base + 1, date, text, removed, request_id, actor)
            view = self._view(db, enrollment)
            revision = self._latest(db, note_id)["revision"]
            db.commit()
        # A deleted note is not in the list; its revision lets the page's Undo restore it.
        return {**view, "note_id": note_id, "note_revision": revision}

    def revise(self, note_id, data, actor="workspace"):
        """Changes a note's date or words."""
        data = data if isinstance(data, dict) else {}

        def change(current):
            if current["removed"]:
                raise Conflict("This note was deleted. Restore it before changing it.")
            return (_date(data["date"]) if "date" in data else current["note_date"],
                    _text(data["text"]) if "text" in data else current["body"], False)
        return self._change(note_id, data, actor, change)

    def remove(self, note_id, data, actor="workspace"):
        """Deletes a note from the student's list (a revision that marks it removed; it can be restored)."""
        return self._change(note_id, data, actor, lambda current: (current["note_date"], current["body"], True))

    def restore(self, note_id, data, actor="workspace"):
        """Puts a deleted note back on the student's list."""
        return self._change(note_id, data, actor, lambda current: (current["note_date"], current["body"], False))
