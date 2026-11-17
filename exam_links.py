"""Links between DR exam bookings and course-wide exam assignments.

A booking names an exam the way the DR center wrote it ("Exam 1", "Final Exam").
Faculty matches that name to the course's assignment titles with "test" and
"exam" interchangeable ("Test 1" and "Exam 1" are the same exam), creates the
assignment when nothing matches, and records one link per booking. Seating
charts exclude students through these links. The instructor can redirect the
bookings of one assignment to another explicitly; nothing else changes a link.
"""

import json
import re
import uuid

from course_storage import timestamp
from document_identity.service import DocumentService
from rosters import connect

FILLER = {"test", "tests", "exam", "exams", "examination", "no", "number", "the"}
SCHEMA = """
    CREATE TABLE IF NOT EXISTS dr_exam_assignment_links (
        booking_id TEXT PRIMARY KEY REFERENCES dr_exam_bookings(id),
        assignment_id TEXT NOT NULL REFERENCES assignments(id),
        method TEXT NOT NULL CHECK(method IN ('matched', 'created', 'manual')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS dr_exam_assignment_links_assignment ON dr_exam_assignment_links(assignment_id);
"""


def canonical(name):
    """'Exam #01', 'Test 1', 'test-1' -> '1'; 'Final Exam', 'Final' -> 'final'; 'Test', 'Exam' -> 'test'."""
    text = re.sub(r"[^\w\s]", " ", str(name or "").casefold().replace("_", " "))
    tokens = [str(int(token)) if token.isdecimal() else token for token in text.split() if token not in FILLER]
    return " ".join(tokens) or "test"


def exam_title(name):
    """The title of an assignment a DR booking creates: the booking's exam name with the word "Test" as "Exam" (instructor,
    2026-09-27: "have DR imports turn 'Test' to 'Exam'"; they call their tests exams). Whole words only, case kept: "Test 4" ->
    "Exam 4", "FINAL TEST" -> "FINAL EXAM", "Tests" -> "Exams"; "Contest" stays. Matching is unchanged: `canonical` already treats
    the two words alike, so a later "Test 4" booking finds "Exam 4"."""
    def swap(found):
        word = found.group(0)
        exam = "exam" + ("s" if word[-1] in "sS" else "")
        return exam.upper() if word.isupper() else exam.capitalize() if word[0].isupper() else exam
    return re.sub(r"\btests?\b", swap, " ".join(str(name).split()), flags=re.IGNORECASE)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-")[:60] or "exam"


class ExamLinkService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with rosters.lock(), connect(self.path) as db:
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            self.sync(db, actor="startup")

    def sync(self, db, actor="dr-exam-import"):
        """Link every unlinked booking, creating assignments when no title matches.

        The caller holds the roster lock and an open transaction, so an import
        and its links commit together.
        """
        rows = db.execute("""SELECT b.id, b.exam_key, r.snapshot_json, t.definition_id
                             FROM dr_exam_bookings b JOIN dr_exam_revisions r ON r.id = b.revision_id
                             JOIN course_sections s ON s.id = b.section_id JOIN course_terms t ON t.id = s.term_id
                             LEFT JOIN dr_exam_assignment_links l ON l.booking_id = b.id
                             WHERE l.booking_id IS NULL ORDER BY b.rowid""").fetchall()
        summary = {"matched": 0, "created": 0, "created_assignments": []}
        when = timestamp()
        for row in rows:
            exam = json.loads(row["snapshot_json"]).get("exam_type") or row["exam_key"]
            assignment = self.match(db, row["definition_id"], exam)
            method = "matched"
            if assignment is None:
                assignment = self.create_assignment(db, row["definition_id"], exam, actor)
                method = "created"
                summary["created"] += 1
                summary["created_assignments"].append({"id": assignment["id"], "title": assignment["title"], "code": assignment["code"]})
            else:
                summary["matched"] += 1
            db.execute("INSERT INTO dr_exam_assignment_links VALUES (?, ?, ?, ?, ?)", (row["id"], assignment["id"], method, when, when))
        return summary

    @staticmethod
    def match(db, definition_id, exam):
        wanted = canonical(exam)
        for row in db.execute("SELECT * FROM assignments WHERE definition_id = ? ORDER BY created_at, id", (definition_id,)):
            if canonical(row["title"]) == wanted or canonical(row["assignment_key"]) == wanted:
                return dict(row)
        return None

    @staticmethod
    def create_assignment(db, definition_id, exam, actor):
        title = exam_title(exam)[:240]
        base = slug(title)
        key, counter = base, 2
        while db.execute("SELECT 1 FROM assignments WHERE definition_id = ? AND assignment_key = ?", (definition_id, key)).fetchone():
            key, counter = f"{base}-{counter}", counter + 1
        row = {"id": str(uuid.uuid4()), "definition_id": definition_id, "assignment_key": key, "title": title, "created_at": timestamp()}
        db.execute("INSERT INTO assignments VALUES (?, ?, ?, ?, ?)", tuple(row.values()))
        DocumentService.event(db, "assignment_created", actor, {"key": key, "source": "dr_exam_booking", "exam_type": " ".join(str(exam).split())[:240]}, assignment_id=row["id"])
        row["code"] = db.execute("SELECT code FROM course_definitions WHERE id = ?", (definition_id,)).fetchone()[0]
        return row

    @staticmethod
    def summary(db, section_ids):
        """Bookings in these sections grouped by the assignment they are linked to."""
        if not section_ids:
            return []
        placeholders = ",".join("?" * len(section_ids))
        result = {}
        for row in db.execute(f"""SELECT a.id, a.title, a.assignment_key, l.method, COUNT(*) AS bookings, COUNT(DISTINCT b.enrollment_id) AS students
                                  FROM dr_exam_bookings b JOIN dr_exam_assignment_links l ON l.booking_id = b.id
                                  JOIN assignments a ON a.id = l.assignment_id
                                  WHERE b.section_id IN ({placeholders}) GROUP BY a.id, l.method ORDER BY a.title, a.id""", section_ids):
            item = result.setdefault(row["id"], {"assignment_id": row["id"], "title": row["title"], "key": row["assignment_key"],
                                                 "bookings": 0, "students": 0, "methods": {}})
            item["bookings"] += row["bookings"]
            item["students"] += row["students"]
            item["methods"][row["method"]] = row["bookings"]
        return list(result.values())

    @staticmethod
    def excluded(db, section_ids, assignment_id):
        """Enrollments in these sections holding a booking linked to the assignment."""
        if not section_ids or not assignment_id:
            return set()
        placeholders = ",".join("?" * len(section_ids))
        return {row[0] for row in db.execute(
            f"""SELECT b.enrollment_id FROM dr_exam_bookings b JOIN dr_exam_assignment_links l ON l.booking_id = b.id
                WHERE l.assignment_id = ? AND b.section_id IN ({placeholders})""", (assignment_id, *section_ids))}

    @staticmethod
    def relink(db, from_assignment_id, to_assignment_id, actor="workspace", term_id=None):
        """Redirect every booking linked to one assignment of a course to another assignment of that course.

        With `term_id`, only that term's bookings move: assignments are shared
        across terms, and another term's charts keep their own exclusions.
        """
        source = db.execute("SELECT * FROM assignments WHERE id = ?", (from_assignment_id,)).fetchone()
        target = db.execute("SELECT * FROM assignments WHERE id = ?", (to_assignment_id,)).fetchone()
        if source is None or target is None:
            raise ValueError("Choose retained exam assignments to link.")
        if source["definition_id"] != target["definition_id"]:
            raise ValueError("DR bookings can only be linked to an assignment of the same course.")
        if source["id"] == target["id"]:
            return 0
        when = timestamp()
        scope, values = ("", ()) if term_id is None else (
            " AND booking_id IN (SELECT b.id FROM dr_exam_bookings b JOIN course_sections s ON s.id = b.section_id WHERE s.term_id = ?)", (term_id,))
        moved = db.execute(f"UPDATE dr_exam_assignment_links SET assignment_id = ?, method = 'manual', updated_at = ? WHERE assignment_id = ?{scope}",
                           (target["id"], when, source["id"], *values)).rowcount
        if moved:
            detail = {"from_assignment_id": source["id"], "from_title": source["title"], "bookings": moved}
            DocumentService.event(db, "dr_bookings_relinked", actor, detail if term_id is None else {**detail, "term_id": term_id}, assignment_id=target["id"])
        return moved
