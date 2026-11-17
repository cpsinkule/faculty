"""Exam seating charts: random seat assignment for the sections taking an exam.

Owner level: course code + term/year, with the seated sections listed
explicitly. A chart references one course-wide assignment
(document_identity.assignments), one workspace room, and permanent section and
enrollment identities. It never copies roster or chart data.

Students who hold a DR exam booking linked to the chart's assignment (see
exam_links.py) are never seated. Bookings linked to another assignment of the
course are reported so the instructor can redirect them explicitly.
"""

import contextlib
import datetime as dt
import io
import hashlib
import json
import re
import secrets
import uuid
from functools import partial

from flask import Blueprint, jsonify, request, send_file
from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from certified_archive import certified, facts as archive_facts
from course_storage import timestamp
from document_identity.media import DEFAULT_MEDIA
from document_identity.service import DocumentService
from request_limits import json_object
from rooms import seat_neighbors, seat_runs, spaced_capacity
from response_cards.mail import twelve_hour
from response_cards.schema import UNCHECKED_FIELDS, personalize, unchecked_dropped, validate_schema
from response_cards.service import EXAM_OVER, closed_statuses
from rosters import connect
from scheduling.clock import CAMPUS_ZONE
from seating_gmail import SeatingGmail
from seating_mail_merge import SeatingMailMerge, _comparable
from seating_versions import automatic_versions, snapshot as exam_version_snapshot, unequal_counts, version_count

STALE = "This seating chart changed on another device. Reload it to get the latest version."
# Response Card templates Faculty manages for exam notices. They belong to the course
# definition like any template and can be edited there; each confirmation freezes
# the current revision. The instructor's explicit confirmation chooses whether
# those issued cards are also published and emailed.
UIN_FIELD = {"id": "confirm_uin", "type": "short_text", "required": True,
             "label": "Type your UIN to confirm that you have read and understood all of the above.",
             "verification": {"expected": "{{student.uin | uin_digest}}", "mode": "uin-digest", "on_mismatch": "BLOCK_SUBMISSION"}}
MANAGED_TEMPLATES = {
    "seat": {"name": "Exam seat assignment (Faculty)", "schema": {
        "title": "{{exam.name}} · your seat assignment",
        "description": "Your {{exam.name}} for {{course.code}} is on {{exam.date_long}} at {{exam.time}} in {{exam.room}}. "
                       "Your assigned seat is {{seat.number}}. Please go directly to that seat and bring your student ID.",
        "confirmation_message": "Thank you. See you at seat {{seat.number}} in {{exam.room}}.",
        "fields": [
            {"id": "confirm_room", "type": "short_text", "required": True, "label": "Please confirm your building and room number as shown above:",
             "verification": {"expected": "{{exam.room}}", "mode": "compact", "on_mismatch": "BLOCK_SUBMISSION"}},
            {"id": "confirm_seat", "type": "short_text", "required": True, "label": "Please confirm your assigned seat as shown above:",
             "verification": {"expected": "{{seat.number}}", "mode": "compact", "on_mismatch": "BLOCK_SUBMISSION"}},
            UIN_FIELD]}},
    "seat_update": {"name": "Updated exam seat assignment (Faculty)", "schema": {
        "title": "{{exam.name}} · updated seat assignment",
        "description": "Your seat for {{exam.name}} in {{exam.room}} has been updated to {{seat.number}}.",
        "confirmation_message": "Thank you. Your updated seat assignment has been confirmed.",
        "fields": [UIN_FIELD]}},
    "dr": {"name": "DR testing notice (Faculty)", "schema": {
        "title": "{{exam.name}} · Disability Resource Center",
        "description": "Records show that you have registered to take {{assignment.name}} with the Disability Resource Center. "
                       "You MUST take your exam as scheduled with the Disability Resource Center. "
                       "If this is an error, you must inform Professor Sinkule immediately.",
        "confirmation_message": "Thank you. Your response has been recorded.",
        "fields": [
            {"id": "confirm_dr_date", "type": "date", "required": True, "label": "Confirm the date of your scheduled DR exam:",
             "verification": {"expected": "{{dr_exam.date}}", "mode": "exact", "on_mismatch": "ALLOW_AND_FLAG"}},
            {"id": "confirm_dr_time", "type": "time", "required": True, "label": "Confirm the start time of your scheduled DR exam:",
             "verification": {"expected": "{{dr_exam.time}}", "mode": "exact", "on_mismatch": "ALLOW_AND_FLAG"}},
            # Asked but never checked: the DR center moves locations too often for a difference to mean anything
            # (RESPONSE-CARDS "DR LOCATION IS NOT CHECKED").
            {"id": "confirm_dr_location", "type": "short_text", "required": True, "label": "Confirm the location (campus) of your scheduled DR exam:"},
            UIN_FIELD]}},
}


def with_uin_confirmation(schema):
    """Every card ends with the UIN confirmation, whatever the instructor edited; a DR card's location answer is never
    checked (RESPONSE-CARDS "DR LOCATION IS NOT CHECKED")."""
    fields = [field if field.get("id") not in UNCHECKED_FIELDS else {k: v for k, v in field.items() if k != "verification"}
              for field in schema.get("fields", []) if field.get("id") != "confirm_uin"
              and (field.get("verification") or {}).get("mode") != "uin-digest"]
    return {**schema, "fields": fields + [json.loads(json.dumps(UIN_FIELD))]}
SCHEMA = """
    CREATE TABLE IF NOT EXISTS seating_charts (
        id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
        term_id TEXT NOT NULL REFERENCES course_terms(id), room_id TEXT NOT NULL REFERENCES rooms(id),
        dr_exam_key TEXT NOT NULL DEFAULT '',
        dr_confirmed_none INTEGER NOT NULL DEFAULT 0 CHECK(dr_confirmed_none IN (0, 1)),
        notes TEXT NOT NULL DEFAULT '', counts_json TEXT NOT NULL DEFAULT '{}',
        check_json TEXT NOT NULL DEFAULT '{}', checked_at TEXT NOT NULL DEFAULT '',
        confirmed_at TEXT NOT NULL DEFAULT '', confirmed_mode TEXT NOT NULL DEFAULT '', confirmed_version_id TEXT,
        cards_json TEXT NOT NULL DEFAULT '{}', exam_date TEXT NOT NULL DEFAULT '', exam_time TEXT NOT NULL DEFAULT '',
        exam_version_count INTEGER CHECK(exam_version_count BETWEEN 1 AND 26),
        allow_unequal_versions INTEGER NOT NULL DEFAULT 0 CHECK(allow_unequal_versions IN (0, 1)),
        -- Charts of one exam created as a mix share a pool: their sections' students are spread at random across those rooms.
        pool_id TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1, generated_at TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS seating_chart_sections (
        chart_id TEXT NOT NULL REFERENCES seating_charts(id) ON DELETE CASCADE,
        section_id TEXT NOT NULL REFERENCES course_sections(id),
        PRIMARY KEY(chart_id, section_id)
    );
    CREATE TABLE IF NOT EXISTS seating_assignments (
        chart_id TEXT NOT NULL REFERENCES seating_charts(id) ON DELETE CASCADE,
        seat_id TEXT NOT NULL REFERENCES room_seats(id) ON DELETE CASCADE,
        section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL, assigned_at TEXT NOT NULL,
        PRIMARY KEY(chart_id, seat_id), UNIQUE(chart_id, enrollment_id),
        FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
    );
    CREATE INDEX IF NOT EXISTS seating_assignments_seat ON seating_assignments(seat_id);
    -- Paper-variant letters are chart-owned seat attributes, independent of
    -- student placement and document_identity's canonical assignment versions.
    CREATE TABLE IF NOT EXISTS seating_exam_versions (
        chart_id TEXT NOT NULL REFERENCES seating_charts(id) ON DELETE CASCADE,
        seat_id TEXT NOT NULL REFERENCES room_seats(id) ON DELETE CASCADE,
        exam_version TEXT NOT NULL CHECK(length(exam_version) = 1 AND exam_version BETWEEN 'A' AND 'Z'),
        PRIMARY KEY(chart_id, seat_id)
    );
    CREATE INDEX IF NOT EXISTS seating_exam_versions_seat ON seating_exam_versions(seat_id);
    CREATE TABLE IF NOT EXISTS seating_events (
        id INTEGER PRIMARY KEY, chart_id TEXT NOT NULL REFERENCES seating_charts(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    -- A current notice revision belongs to the existing term-owned chart and
    -- section/enrollment membership. Delivery stays owned by its response card.
    CREATE TABLE IF NOT EXISTS seating_card_updates (
        chart_id TEXT NOT NULL REFERENCES seating_charts(id) ON DELETE CASCADE,
        enrollment_id TEXT NOT NULL, section_id TEXT NOT NULL,
        seat_id TEXT REFERENCES room_seats(id) ON DELETE SET NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        card_id TEXT REFERENCES response_card_recipients(id),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(chart_id,enrollment_id),
        FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
    );
"""


def exam_name(title):
    """Titles compare without case or spacing differences; 'Test 1' and 'Exam 1' stay different names."""
    return " ".join(str(title or "").split()).casefold()


def term_label(row):
    return f"{row['term_other'] if row['term'] == 'Other' else row['term']} {row['year']}"


def seat_order(label):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", label)]


def student_name(student):
    fields = student.get("fields") or {}
    for key, value in fields.items():
        if key.casefold() == "name" and str(value).strip():
            return str(value).strip()
    return next((str(value).strip() for value in fields.values() if str(value).strip()), student.get("id", ""))


def recorded_time(value):
    """A stored moment on the campus clock, as the chart page shows recorded times: 'Sat, Sep 26 · 8:43 AM'."""
    try:
        moment = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value or "")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    try:
        moment = moment.astimezone(CAMPUS_ZONE)
    except Exception:  # pragma: no cover - missing tz database
        moment = moment.astimezone(dt.timezone.utc)
    return f"{moment.strftime('%a, %b')} {moment.day} · {moment.strftime('%I:%M %p').lstrip('0')}"


class SeatingService:
    def __init__(self, rosters, rooms, links):
        self.rosters, self.rooms, self.links, self.path = rosters, rooms, links, rosters.path
        self.documents = None  # Set by the app; confirmation exports print jobs through it.
        self.response_cards = None  # Set by the app; confirmation prepares and optionally sends notices through it.
        with rosters.lock(), connect(self.path) as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(seating_charts)")}
            for column, definition in (("check_json", "TEXT NOT NULL DEFAULT '{}'"), ("checked_at", "TEXT NOT NULL DEFAULT ''"),
                                       ("confirmed_at", "TEXT NOT NULL DEFAULT ''"), ("confirmed_mode", "TEXT NOT NULL DEFAULT ''"),
                                       ("confirmed_version_id", "TEXT"), ("cards_json", "TEXT NOT NULL DEFAULT '{}'"),
                                       ("exam_date", "TEXT NOT NULL DEFAULT ''"), ("exam_time", "TEXT NOT NULL DEFAULT ''"),
                                       ("exam_version_count", "INTEGER CHECK(exam_version_count BETWEEN 1 AND 26)"),
                                       ("allow_unequal_versions", "INTEGER NOT NULL DEFAULT 0 CHECK(allow_unequal_versions IN (0, 1))"),
                                       ("pool_id", "TEXT NOT NULL DEFAULT ''")):
                if column not in columns:
                    db.execute(f"ALTER TABLE seating_charts ADD COLUMN {column} {definition}")

    # ----- roster check ----------------------------------------------------------------

    @staticmethod
    def _check(db, chart_id, assignment_id, section_ids):
        """Compare the seat assignments with the roster and DR bookings, independently of how seats were chosen.

        Flags: a student with a DR booking for this exam who has a seat; a roster
        student without a booking who has no seat; a seated student no longer in
        the sections; a seated student on a broken or blocked seat.
        """
        placeholders = ",".join("?" * len(section_ids)) or "''"
        # A mixed chart's student may hold a seat in another room of its mix.
        pool = SeatingService._pool(db, chart_id)
        pooled = ",".join("?" * len(pool))
        booked = f"""SELECT b.enrollment_id FROM dr_exam_bookings b JOIN dr_exam_assignment_links l ON l.booking_id = b.id
                     WHERE l.assignment_id = ? AND b.section_id IN ({placeholders})"""
        dr_seated = [dict(row) for row in db.execute(
            f"""SELECT a.enrollment_id, a.seat_id FROM seating_assignments a WHERE a.chart_id = ? AND a.enrollment_id IN ({booked})""",
            (chart_id, assignment_id, *section_ids))]
        # A Q drop or withdrawal on the registrar's roster (`roster_leaving`) is owed no seat.
        missing_seat = [row[0] for row in db.execute(
            f"""SELECT DISTINCT m.enrollment_id FROM roster_current m WHERE m.section_id IN ({placeholders})
                AND m.enrollment_id NOT IN (SELECT enrollment_id FROM seating_assignments WHERE chart_id IN ({pooled}))
                AND m.enrollment_id NOT IN ({booked})
                AND NOT EXISTS(SELECT 1 FROM roster_leaving l WHERE l.section_id=m.section_id AND l.enrollment_id=m.enrollment_id)
                ORDER BY m.enrollment_id""",
            (*section_ids, *pool, assignment_id, *section_ids))]
        not_in_roster = [dict(row) for row in db.execute(
            f"""SELECT a.enrollment_id, a.seat_id FROM seating_assignments a WHERE a.chart_id = ?
                AND a.enrollment_id NOT IN (SELECT enrollment_id FROM roster_memberships WHERE section_id IN ({placeholders}))""",
            (chart_id, *section_ids))]
        seat_unusable = [dict(row) for row in db.execute(
            """SELECT a.enrollment_id, a.seat_id, s.label, s.broken, s.blocked FROM seating_assignments a JOIN room_seats s ON s.id = a.seat_id
               WHERE a.chart_id = ? AND (s.broken = 1 OR s.blocked = 1)""", (chart_id,))]
        result = {"dr_seated": dr_seated, "missing_seat": missing_seat, "not_in_roster": not_in_roster, "seat_unusable": seat_unusable}
        room = db.execute("SELECT r.review_required FROM rooms r JOIN seating_charts c ON c.room_id=r.id WHERE c.id=?", (chart_id,)).fetchone()
        result["room_review"] = json.loads(room[0]) if room else []
        result["counts"] = {key: len(value) for key, value in result.items() if key != "room_review" or value}
        result["ok"] = not any(result["counts"].values())
        return result

    # ----- mixed rooms -----------------------------------------------------------------

    @staticmethod
    def _pool(db, chart_id):
        """The charts of this chart's mix in creation order, itself included; a chart that is not mixed is alone.

        The first chart of a mix lists the DR-booked students (and prepares their cards), so they appear once.
        """
        row = db.execute("SELECT pool_id FROM seating_charts WHERE id = ?", (chart_id,)).fetchone()
        if row is None or not row[0]:
            return [chart_id]
        return [item[0] for item in db.execute("SELECT id FROM seating_charts WHERE pool_id = ? ORDER BY rowid", (row[0],))]

    @staticmethod
    def _seated_elsewhere(db, chart_id, pool=None):
        """Students holding a seat in another room of this chart's mix: enrollment -> that chart's room name."""
        others = [item for item in (pool or SeatingService._pool(db, chart_id)) if item != chart_id]
        if not others:
            return {}
        return {row[0]: row[1] for row in db.execute(
            f"""SELECT a.enrollment_id, r.name FROM seating_assignments a JOIN seating_charts c ON c.id = a.chart_id
                JOIN rooms r ON r.id = c.room_id WHERE a.chart_id IN ({','.join('?' * len(others))})""", others)}

    @staticmethod
    def pool_shares(rooms, total):
        """How many students each room of a mix seats: shares of the rooms' spaced seats while everyone can have an empty seat
        beside them, otherwise of their usable seats. Every room seats somebody when there are enough students."""
        usable = sum(room["usable_count"] for room in rooms)
        if total > usable:
            raise Conflict(f"{total} student{'s' if total != 1 else ''} need seats but these rooms have only {usable} usable seats together. "
                           "Add a room, seat fewer sections, or unblock seats.")
        key = "spaced_count" if total <= sum(room["spaced_count"] for room in rooms) else "usable_count"
        weights = [room[key] for room in rooms]
        whole = sum(weights) or 1
        shares = [total * weight // whole for weight in weights]
        # Largest remainders first (whole numbers, so ties are exact), the larger room on a tie.
        for index in sorted(range(len(rooms)), key=lambda index: (total * weights[index] % whole, weights[index]), reverse=True)[:total - sum(shares)]:
            shares[index] += 1
        if total >= len(rooms):
            for index in range(len(rooms)):
                if not shares[index] and rooms[index]["usable_count"]:
                    donor = max(range(len(rooms)), key=lambda other: shares[other])
                    shares[donor] -= 1
                    shares[index] += 1
        return shares

    # ----- reading -----------------------------------------------------------------

    def catalog(self, rooms=True):
        """Assignments, sections and rooms for the seating forms. `rooms=False` leaves the rooms out (their spaced capacity is most
        of the cost) for callers that show none, such as the bubble sheet panel."""
        with self.rosters.lock(), connect(self.path) as db:
            assignments = [dict(row) for row in db.execute(
                "SELECT a.id, a.definition_id, a.assignment_key, a.title, d.code FROM assignments a JOIN course_definitions d ON d.id = a.definition_id ORDER BY d.code, a.title")]
            sections = [{**dict(row), "term_label": term_label(row), "is_current": bool(row["is_current"])} for row in db.execute(
                """SELECT s.id, s.number, t.id AS term_id, t.definition_id, t.code, t.term, t.term_other, t.year, t.is_current
                   FROM course_sections s JOIN course_terms t ON t.id = s.term_id WHERE t.deleted_at IS NULL
                   ORDER BY t.is_current DESC, t.code, t.year DESC, t.term, s.number""")]
            result = {"assignments": assignments, "sections": sections}
            if rooms:
                result["rooms"] = [{key: room[key] for key in ("id", "name", "seat_count", "usable_count", "broken_count", "blocked_count", "spaced_count", "review_required")}
                                   for room in self.rooms.list(db)]
        return result

    def list(self, db=None, chart_id=None):
        """Every chart, newest first; with `chart_id`, that chart alone (the same entry, for `get`)."""
        if db is None:
            with connect(self.path) as db:
                return self.list(db, chart_id)
        rows = db.execute(f"""SELECT c.*, a.title, a.assignment_key, a.definition_id, d.code, t.term, t.term_other, t.year, r.name AS room_name,
                                    (SELECT COUNT(*) FROM seating_assignments x WHERE x.chart_id = c.id) AS seated_count
                             FROM seating_charts c JOIN assignments a ON a.id = c.assignment_id
                             JOIN course_definitions d ON d.id = a.definition_id
                             JOIN course_terms t ON t.id = c.term_id JOIN rooms r ON r.id = c.room_id
                             {"WHERE c.id = ?" if chart_id is not None else ""}
                             ORDER BY c.created_at DESC, c.id""", () if chart_id is None else (chart_id,)).fetchall()
        result = []
        archive = certified(db)  # certified_archive.py: the list folds certified assessments into Archived (a view)
        for row in rows:
            members = db.execute(
                "SELECT s.id, s.number FROM seating_chart_sections x JOIN course_sections s ON s.id = x.section_id WHERE x.chart_id = ? ORDER BY s.number", (row["id"],)).fetchall()
            item = dict(row)
            item["counts"] = json.loads(item.pop("counts_json") or "{}")
            item["cards"] = json.loads(item.pop("cards_json") or "{}")
            item["term_label"] = term_label(row)
            item["sections"] = [member["number"] for member in members]
            item["archived"] = archive_facts(archive.get((row["assignment_id"], row["term_id"])), row["created_at"])
            item["section_ids"] = [member["id"] for member in members]
            # The other rooms a mixed chart shares its sections' students with.
            item["pool_rooms"] = [other[0] for other in db.execute(
                "SELECT r.name FROM seating_charts c JOIN rooms r ON r.id = c.room_id WHERE c.pool_id = ? AND c.id != ? ORDER BY c.rowid",
                (row["pool_id"], row["id"]))] if row["pool_id"] else []
            # The room whose chart lists the mix's DR students, when it is another room's.
            first = self._pool(db, row["id"])[0]
            item["pool_dr_room"] = db.execute("SELECT r.name FROM seating_charts c JOIN rooms r ON r.id = c.room_id WHERE c.id = ?",
                                              (first,)).fetchone()[0] if first != row["id"] else ""
            item["term_sections"] = [number[0] for number in db.execute("SELECT number FROM course_sections WHERE term_id = ? ORDER BY number", (row["term_id"],))]
            item["print_jobs"] = self._print_jobs(db, row["assignment_id"], row["term_id"])
            live = self._check(db, row["id"], row["assignment_id"], item["section_ids"])
            item["check"] = {"ok": live["ok"], "counts": live["counts"]}
            item["checked"] = json.loads(item.pop("check_json") or "{}")
            item["delete_confirmation"] = "typed" if item["print_jobs"] or item["confirmed_at"] else "yes_no"
            item["dr_confirmed_none"] = bool(item["dr_confirmed_none"])
            item["allow_unequal_versions"] = bool(item["allow_unequal_versions"])
            # Read-only list additions (UX overhaul): response-card and exam-version totals for the list's status chips.
            item["card_counts"] = self._card_counts(db, item["cards"])
            # Seated students moved after confirmation whose updated seat card has no accepted invitation with a provider
            # receipt: the chart's "Seat update not sent" and the sidebar's seat-update count (count only).
            item["seat_updates_pending"] = db.execute("""SELECT COUNT(*) FROM seating_card_updates u
                JOIN seating_assignments s ON s.chart_id=u.chart_id AND s.enrollment_id=u.enrollment_id AND s.seat_id=u.seat_id
                WHERE u.chart_id=? AND u.seat_id IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM response_card_recipients r WHERE r.id=u.card_id
                    AND (r.status!='revoked' OR EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id AND x.reason=?))
                    AND EXISTS(SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='invitation' AND m.status='accepted'
                               AND COALESCE(m.provider_receipt_json,'')!=''))""", (row["id"], EXAM_OVER)).fetchone()[0] \
                if item["confirmed_at"] and self.response_cards else 0
            item["version_totals"] = {letter: count for letter, count in db.execute(
                """SELECT v.exam_version, COUNT(*) FROM seating_exam_versions v
                   JOIN seating_assignments x ON x.chart_id = v.chart_id AND x.seat_id = v.seat_id
                   WHERE v.chart_id = ? GROUP BY v.exam_version ORDER BY v.exam_version""", (row["id"],))}
            result.append(item)
        return result

    @staticmethod
    def _print_jobs(db, assignment_id, term_id):
        """Non-cancelled print jobs (Data Matrix page stamps) for any version of the exam in this term."""
        return db.execute("""SELECT COUNT(*) FROM print_batches b JOIN assignment_versions v ON v.id = b.assignment_version_id
                             WHERE v.assignment_id = ? AND b.term_id = ? AND b.status != 'cancelled'""", (assignment_id, term_id)).fetchone()[0]

    def _chart(self, db, chart_id):
        row = db.execute("SELECT * FROM seating_charts WHERE id = ?", (chart_id,)).fetchone()
        if row is None:
            raise NotFound("This seating chart no longer exists.")
        return dict(row)

    def _check_version(self, db, chart_id, version):
        chart = self._chart(db, chart_id)
        if type(version) is not int or version != chart["version"]:
            raise Conflict(STALE)
        return chart

    def _sections(self, db, chart_id):
        return [dict(row) for row in db.execute(
            """SELECT s.id, s.number, s.term_id FROM seating_chart_sections x JOIN course_sections s ON s.id = x.section_id
               WHERE x.chart_id = ? ORDER BY s.number""", (chart_id,))]

    def _students(self, db, term_id, sections):
        """Roster members of the given sections: enrollment -> name, uin, section. Needs the roster lock."""
        term = db.execute("SELECT * FROM course_terms WHERE id = ?", (term_id,)).fetchone()
        if term is None:
            raise NotFound("The course term for this chart no longer exists.")
        with self.rosters.files(dict(term)) as root:
            payload = self.rosters.read_payload(root, term["roster_initialized"])
        students = {}
        dropped = {(row[0], row[1]) for row in db.execute(
            f"SELECT section_id,enrollment_id FROM roster_dropped WHERE section_id IN ({','.join('?' * len(sections)) or "''"})",
            [section["id"] for section in sections])}
        leaving = {(row[0], row[1]): row[2] for row in db.execute(
            f"SELECT section_id,enrollment_id,reason FROM roster_leaving WHERE section_id IN ({','.join('?' * len(sections)) or "''"})",
            [section["id"] for section in sections])}
        for section in sections:
            names = payload["sections"].get(section["id"], {}).get("students", {})
            for row in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id = ?", (section["id"],)):
                enrollment_id = row[0]
                if enrollment_id in students:
                    continue  # Also enrolled in another selected section; seat once.
                record = names.get(enrollment_id, {})
                students[enrollment_id] = {"enrollment_id": enrollment_id, "section_id": section["id"], "section_number": section["number"],
                                           "name": student_name({**record, "id": enrollment_id}), "uin": record.get("uin", ""),
                                           # Dropped (rosters.set_dropped): never newly seated; a seat they already hold is kept.
                                           "dropped": (section["id"], enrollment_id) in dropped,
                                           # A Q drop or withdrawal on the registrar's roster (requirement, 2026-10-15): never seated,
                                           # and a seat they hold is given up at the next update.
                                           "leaving": leaving.get((section["id"], enrollment_id), "")}
        return students

    def _dr(self, db, section_ids, assignment_id, students):
        """DR bookings in these sections: those linked to the assignment and those linked elsewhere."""
        excluded = self.links.excluded(db, section_ids, assignment_id) & set(students)
        others = [item for item in self.links.summary(db, section_ids) if item["assignment_id"] != assignment_id]
        for item in others:
            item["auto_created"] = set(item["methods"]) == {"created"}
        return {"excluded_students": len(excluded), "others": others}, excluded

    def options(self, assignment_id, section_ids, room_ids=None):
        """What a new chart would seat: eligible students and the DR bookings that apply.

        With `room_ids`, the sections are mixed across those rooms: `pool` lists how many students each room would seat."""
        with self.rosters.lock(), connect(self.path) as db:
            assignment, sections, term_id = self._validate_scope(db, assignment_id, section_ids)
            students = {key: value for key, value in self._students(db, term_id, sections).items() if not value["dropped"] and not value["leaving"]}
            dr, excluded = self._dr(db, [section["id"] for section in sections], assignment["id"], students)
            per_section = {section["id"]: {"number": section["number"], "students": 0, "excluded": 0} for section in sections}
            for enrollment_id, student in students.items():
                per_section[student["section_id"]]["students"] += 1
                per_section[student["section_id"]]["excluded"] += enrollment_id in excluded
            result = {"students": len(students), "assignment_title": assignment["title"], "dr": dr, "per_section": per_section,
                      "term_id": term_id, "sections": [section["number"] for section in sections]}
            if room_ids:
                rooms = self._pool_rooms(db, room_ids)
                try:
                    shares = self.pool_shares(rooms, len(students) - len(excluded))
                    result["pool"] = [{"room_id": room["id"], "to_seat": share} for room, share in zip(rooms, shares)]
                except Conflict as error:
                    result["pool"], result["pool_error"] = [], error.description
            return result

    def _pool_rooms(self, db, room_ids):
        """The rooms chosen for a mix, checked: two or more distinct rooms, each ready for seating."""
        if not isinstance(room_ids, list) or not 2 <= len(room_ids) <= 10 or any(not isinstance(item, str) for item in room_ids):
            raise ValueError("Choose at least two rooms to mix the sections across.")
        if len(set(room_ids)) != len(room_ids):
            raise ValueError("Choose each room once.")
        rooms = []
        for room_id in room_ids:
            try:
                room = self.rooms.get(room_id, db, seats=False)
            except NotFound:
                raise ValueError("Choose a room with an uploaded seating chart.") from None
            self._require_room_ready(room)
            rooms.append(room)
        return rooms

    def attachable(self, definition_id, section_ids, title=""):
        """Existing seating plans of a course and term, compared with the sections and title chosen for an assignment.

        A plan is every chart of one exam assignment in the term. Nothing is
        written here. A plan is incompatible when it seats a section outside
        the chosen ones; chosen sections it leaves out are reported for the
        instructor to seat or drop. `rename_blockers` lists why a plan has to
        keep its own exam name instead of moving to the typed assignment.
        """
        if not isinstance(definition_id, str) or not definition_id:
            raise ValueError("Choose the course this assignment belongs to.")
        if not isinstance(section_ids, list) or not section_ids or len(section_ids) > 50 or any(not isinstance(item, str) for item in section_ids):
            raise ValueError("Choose at least one section for this assignment.")
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("Each section can be chosen once.")
        with connect(self.path) as db:
            placeholders = ",".join("?" * len(section_ids))
            sections = [dict(row) for row in db.execute(
                f"""SELECT s.id, s.number, s.term_id, t.definition_id FROM course_sections s JOIN course_terms t ON t.id = s.term_id
                    WHERE s.id IN ({placeholders}) AND t.deleted_at IS NULL ORDER BY s.number""", section_ids)]
            if len(sections) != len(section_ids):
                raise ValueError("One of the chosen sections no longer exists.")
            if len({section["term_id"] for section in sections}) != 1:
                raise ValueError("Choose sections from one course and term.")
            if sections[0]["definition_id"] != definition_id:
                raise ValueError("The chosen sections belong to a different course.")
            chosen = {section["id"]: section["number"] for section in sections}
            plans = {}
            for chart in self.list(db):
                if chart["definition_id"] != definition_id or chart["term_id"] != sections[0]["term_id"]:
                    continue
                plan = plans.setdefault(chart["assignment_id"], {
                    "assignment_id": chart["assignment_id"], "title": chart["title"], "assignment_key": chart["assignment_key"],
                    "charts": [], "seated": {}, "rename_blockers": []})
                plan["charts"].append({"id": chart["id"], "room_name": chart["room_name"], "sections": chart["sections"],
                                       "section_ids": chart["section_ids"], "seated_count": chart["seated_count"],
                                       "confirmed_at": chart["confirmed_at"], "check_ok": chart["check"]["ok"]})
                plan["seated"].update(zip(chart["section_ids"], chart["sections"]))
                plan["rename_blockers"] += [f"{chart['room_name']}: {reason}" for reason in self._move_blockers(db, chart["id"])]
        wanted = exam_name(title)
        result = []
        for plan in plans.values():
            seated = plan.pop("seated")
            plan["covered"] = sorted(number for section, number in seated.items() if section in chosen)
            plan["extra"] = sorted(number for section, number in seated.items() if section not in chosen)
            plan["uncovered"] = sorted(number for section, number in chosen.items() if section not in seated)
            plan["uncovered_ids"] = [section for section in chosen if section not in seated]
            plan["compatible"] = not plan["extra"]
            plan["name_conflict"] = bool(wanted) and exam_name(plan["title"]) != wanted
            plan["charts"].sort(key=lambda item: item["room_name"].casefold())
            result.append(plan)
        result.sort(key=lambda item: (not item["compatible"], item["name_conflict"], item["title"].casefold()))
        return {"term_id": sections[0]["term_id"], "plans": result}

    @staticmethod
    def _move_blockers(db, chart_id):
        """Records that carry a chart's exam: once any exists, the chart stays with its assignment."""
        chart = db.execute("SELECT confirmed_at FROM seating_charts WHERE id = ?", (chart_id,)).fetchone()
        reasons = ["it is confirmed for printing"] if chart["confirmed_at"] else []
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, column, reason in (("print_batches", "seating_chart_id", "print jobs were created from it"),
                                      ("response_card_issues", "seating_chart_id", "response cards were issued from it"),
                                      ("seating_student_mail", "chart_id", "student seating charts were emailed from it"),
                                      ("exam_event_charts", "chart_id", "it is linked to an exam")):
            if table in tables and db.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", (chart_id,)).fetchone():
                reasons.append(reason)
        return reasons

    def move_plan(self, data):
        """Attach an exam's unconfirmed charts of one term to another assignment of the course, under that assignment's name.

        Assignments are retained identities and are never renamed. The charts
        move, and the earlier assignment's DR bookings of that term follow
        through the explicit relink used by chart settings, so the same
        students stay excluded; another term's charts and bookings stay with
        the earlier assignment. A later DR export under the earlier name links
        to the earlier assignment again and appears as a chart notice to relink.
        """
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object.")
        chart_ids = data.get("chart_ids")
        if not isinstance(chart_ids, list) or not chart_ids or len(chart_ids) > 10 or any(not isinstance(item, str) for item in chart_ids) or len(set(chart_ids)) != len(chart_ids):
            raise ValueError("Choose the seating charts to attach.")
        when = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute("SELECT * FROM assignments WHERE id = ?", (data.get("assignment_id"),)).fetchone()
            if target is None:
                raise NotFound("That exam assignment no longer exists.")
            charts = [self._chart(db, chart_id) for chart_id in chart_ids]
            source_ids = {chart["assignment_id"] for chart in charts}
            if len(source_ids) != 1 or len({chart["term_id"] for chart in charts}) != 1:
                raise ValueError("Attach the seating charts of one exam and term together.")
            source = db.execute("SELECT * FROM assignments WHERE id = ?", (source_ids.pop(),)).fetchone()
            if source["id"] == target["id"]:
                return {"charts": [self.get(chart["id"], db) for chart in charts], "relinked_bookings": 0}
            if source["definition_id"] != target["definition_id"]:
                raise ValueError("A seating chart can only be attached to an assignment of the same course.")
            remaining = db.execute(f"SELECT COUNT(*) FROM seating_charts WHERE assignment_id = ? AND term_id = ? AND id NOT IN ({','.join('?' * len(chart_ids))})",
                                   (source["id"], charts[0]["term_id"], *chart_ids)).fetchone()[0]
            if remaining:
                raise Conflict(f"{source['title']} has {remaining} more seating chart{'' if remaining == 1 else 's'} this term. Attach every room of the exam together.")
            for chart in charts:
                room = db.execute("SELECT name FROM rooms WHERE id = ?", (chart["room_id"],)).fetchone()["name"]
                reasons = self._move_blockers(db, chart["id"])
                if reasons:
                    raise Conflict(f"The {room} chart keeps the name {source['title']}: {'; '.join(reasons)}. Continue with {source['title']} instead.")
                self._check_overlap(db, target["id"], [section["id"] for section in self._sections(db, chart["id"])])
            moved = self.links.relink(db, source["id"], target["id"], term_id=charts[0]["term_id"])
            detail = {"from_assignment_id": source["id"], "from_title": source["title"], "to_assignment_id": target["id"], "to_title": target["title"],
                      "relinked_bookings": moved}
            for chart in charts:
                db.execute("UPDATE seating_charts SET assignment_id = ?, updated_at = ?, version = version + 1 WHERE id = ?", (target["id"], when, chart["id"]))
                self.record_event(db, chart["id"], "exam_changed", detail, when)
            DocumentService.event(db, "seating_charts_attached", "workspace", {**detail, "chart_ids": chart_ids}, assignment_id=target["id"])
            return {"charts": [self.get(chart["id"], db) for chart in charts], "relinked_bookings": moved}

    def print_plan(self, db, chart_id, section_id, assignment_id):
        """Copies to print for one section of a chart: one per seated student, in seat order.

        The caller holds the roster lock. Captions carry the seat, name, section
        and room for the printed stamp; the barcode payload stays the opaque token.
        """
        chart = self._chart(db, chart_id)
        if chart["assignment_id"] != assignment_id:
            raise ValueError("The seating chart belongs to a different exam assignment than this version.")
        sections = self._sections(db, chart_id)
        if section_id not in {section["id"] for section in sections}:
            raise ValueError("The seating chart does not include that section.")
        students = self._students(db, chart["term_id"], sections)
        room = self.rooms.get(chart["room_id"], db)
        self._require_room_ready(room)
        seats = {seat["id"]: seat for seat in room["seats"]}
        copies = []
        for row in db.execute("SELECT * FROM seating_assignments WHERE chart_id = ? AND section_id = ?", (chart_id, section_id)):
            seat, student = seats.get(row["seat_id"]), students.get(row["enrollment_id"])
            if seat is None or student is None:
                continue
            copies.append({"enrollment_id": row["enrollment_id"], "seat_label": seat["label"], "name": student["name"],
                           "caption": f"Seat {seat['label']} · {student['name']} · Sec {student['section_number']} · {room['name']}"[:110]})
        copies.sort(key=lambda copy: seat_order(copy["seat_label"]))
        return {"chart_id": chart_id, "room": room["name"], "copies": copies}

    # ----- validation ----------------------------------------------------------------

    def _validate_scope(self, db, assignment_id, section_ids):
        if not isinstance(assignment_id, str) or not assignment_id:
            raise ValueError("Choose the exam assignment this seating chart is for.")
        assignment = db.execute("SELECT a.*, d.code FROM assignments a JOIN course_definitions d ON d.id = a.definition_id WHERE a.id = ?", (assignment_id,)).fetchone()
        if assignment is None:
            raise ValueError("Choose a retained exam assignment.")
        if not isinstance(section_ids, list) or not section_ids or len(section_ids) > 50 or any(not isinstance(item, str) for item in section_ids):
            raise ValueError("Choose at least one section to seat.")
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("Each section can be chosen once.")
        placeholders = ",".join("?" * len(section_ids))
        sections = [dict(row) for row in db.execute(
            f"""SELECT s.id, s.number, s.term_id, t.definition_id, t.code, t.term, t.term_other, t.year FROM course_sections s
                JOIN course_terms t ON t.id = s.term_id WHERE s.id IN ({placeholders}) AND t.deleted_at IS NULL ORDER BY s.number""", section_ids)]
        if len(sections) != len(section_ids):
            raise ValueError("One of the chosen sections no longer exists.")
        if len({section["term_id"] for section in sections}) != 1:
            raise ValueError("Choose sections from one course and term. Make a separate chart for another term.")
        if sections[0]["definition_id"] != assignment["definition_id"]:
            raise ValueError(f"The chosen sections are {sections[0]['code']}, but the assignment belongs to {assignment['code']}.")
        return dict(assignment), sections, sections[0]["term_id"]

    @staticmethod
    def _check_overlap(db, assignment_id, section_ids):
        """A section is seated once per exam; a second room needs the first chart removed."""
        placeholders = ",".join("?" * len(section_ids))
        rows = db.execute(f"""SELECT s.number, r.name, a.title FROM seating_chart_sections x
                              JOIN seating_charts c ON c.id = x.chart_id JOIN course_sections s ON s.id = x.section_id
                              JOIN rooms r ON r.id = c.room_id JOIN assignments a ON a.id = c.assignment_id
                              WHERE c.assignment_id = ? AND x.section_id IN ({placeholders}) ORDER BY s.number""", (assignment_id, *section_ids)).fetchall()
        if rows:
            listed = "; ".join(f"Section {row['number']} in {row['name']}" for row in rows)
            raise Conflict(f"Already seated for {rows[0]['title']}: {listed}. Remove that chart first, or leave the section out.")

    def _relink(self, db, assignment_id, relink_ids):
        """Redirect other assignments' DR bookings to this exam when the instructor says they are the same."""
        if relink_ids is None:
            return []
        if not isinstance(relink_ids, list) or len(relink_ids) > 20 or any(not isinstance(item, str) for item in relink_ids):
            raise ValueError("Choose which other exams' DR bookings belong to this exam.")
        moved = []
        for other in dict.fromkeys(relink_ids):
            count = self.links.relink(db, other, assignment_id)
            moved.append({"assignment_id": other, "bookings": count})
        return moved

    # ----- seating ---------------------------------------------------------------------

    @staticmethod
    def _place(usable, seated_ids, free, needed, rng):
        """Choose seats for `needed` students: an empty seat between neighbours where the room allows, then the least crowded seats."""
        neighbors = seat_neighbors(list(usable.values()))
        quiet = [seat_id for seat_id in free if not (neighbors[seat_id] & seated_ids)]
        spaced = []
        for kind, run in seat_runs([usable[seat_id] for seat_id in quiet]):
            if kind == "table":
                spaced.append(rng.choice(run)["id"])
            else:
                start = rng.choice((0, 1)) if len(run) % 2 == 0 and len(run) > 1 else 0
                spaced.extend(seat["id"] for seat in run[start::2])
        rng.shuffle(spaced)
        chosen = spaced[:needed]
        taken = set(chosen) | set(seated_ids)
        pool = [seat_id for seat_id in free if seat_id not in taken]
        while len(chosen) < needed and pool:
            crowding = {seat_id: len(neighbors[seat_id] & taken) for seat_id in pool}
            best = min(crowding.values())
            pick = rng.choice([seat_id for seat_id in pool if crowding[seat_id] == best])
            chosen.append(pick)
            taken.add(pick)
            pool.remove(pick)
        return chosen

    @staticmethod
    def spacing(seats, seated_ids):
        """How the seated students are spread: those with nobody beside them, and neighbouring pairs."""
        neighbors = seat_neighbors(seats)
        seated = set(seated_ids)
        crowded = {seat_id for seat_id in seated if neighbors.get(seat_id, set()) & seated}
        pairs = sum(len(neighbors.get(seat_id, set()) & seated) for seat_id in seated) // 2
        return {"seated": len(seated), "spaced": len(seated) - len(crowded), "crowded": len(crowded), "adjacent_pairs": pairs,
                "spaced_capacity": spaced_capacity([seat for seat in seats if not seat.get("broken") and not seat.get("blocked")]),
                "crowded_ids": crowded}

    def _scope(self, db, chart):
        """Who belongs to this chart: its sections' students, less those seated in another room of its mix.

        Returns (sections, students, the DR summary, DR-excluded students, the DR-excluded students this chart lists). A mix lists its DR
        students on its first chart only, so they are counted and given cards once.
        """
        sections = self._sections(db, chart["id"])
        pool = self._pool(db, chart["id"])
        elsewhere = self._seated_elsewhere(db, chart["id"], pool)
        students = {key: value for key, value in self._students(db, chart["term_id"], sections).items() if key not in elsewhere}
        dr, excluded = self._dr(db, [section["id"] for section in sections], chart["assignment_id"], students)
        shown = excluded if pool[0] == chart["id"] else set()
        return sections, students, {**dr, "excluded_students": len(shown)}, excluded, shown

    @staticmethod
    def record_event(db, chart_id, kind, detail, when, **dumps):
        """Append one event to the chart's history; `dumps` are json.dumps options for its detail."""
        db.execute("INSERT INTO seating_events (chart_id, kind, detail_json, created_at) VALUES (?, ?, ?, ?)",
                   (chart_id, kind, json.dumps(detail, **dumps), when))

    def _record_check(self, db, chart_id, assignment_id, section_ids, students, excluded):
        """The roster check and the record of it kept on the chart; `students` and `excluded` are the counts it lists."""
        check = self._check(db, chart_id, assignment_id, section_ids)
        return check, {"ok": check["ok"], "counts": check["counts"], "students": students, "excluded_dr": excluded,
                       "dr_seated": [item["enrollment_id"] for item in check["dr_seated"]], "missing_seat": check["missing_seat"],
                       "not_in_roster": [item["enrollment_id"] for item in check["not_in_roster"]],
                       "seat_unusable": [item["label"] for item in check["seat_unusable"]]}

    def _recheck(self, db, chart_id, when):
        """Record a mixed chart's roster check again once every room of its mix is seated."""
        chart = self._chart(db, chart_id)
        sections, students, _, excluded, shown = self._scope(db, chart)
        listed = {key: value for key, value in students.items() if key not in excluded or key in shown}
        check, checked = self._record_check(db, chart_id, chart["assignment_id"], [section["id"] for section in sections], len(listed), len(shown))
        counts = {**json.loads(chart["counts_json"] or "{}"), "students": len(listed), "excluded_dr": len(shown), "check_ok": check["ok"]}
        db.execute("UPDATE seating_charts SET counts_json = ?, check_json = ?, checked_at = ? WHERE id = ?",
                   (json.dumps(counts), json.dumps(checked), when, chart_id))

    def _seat(self, db, chart, mode, when, limit=None):
        """Assign seats. mode 'regenerate' reshuffles everyone; 'update' keeps valid seats.

        In a mix, students seated in another of its rooms are left where they are; `limit` caps how many this room newly seats
        (its share when the mix is created)."""
        sections, students, _, excluded, shown = self._scope(db, chart)
        section_ids = [section["id"] for section in sections]
        current = [dict(row) for row in db.execute("SELECT * FROM seating_assignments WHERE chart_id = ?", (chart["id"],))]
        holding = {assignment["enrollment_id"] for assignment in current}
        # A dropped student is never newly seated, and a regeneration reseats only current students; an update keeps the seat a
        # dropped student already holds (requirement, 2026-09-27: keep existing records).
        eligible = {key: value for key, value in students.items()
                    if key not in excluded and not value["leaving"] and (not value["dropped"] or (mode == "update" and key in holding))}
        room = self.rooms.get(chart["room_id"], db)
        self._require_room_ready(room)
        usable = {seat["id"]: seat for seat in room["seats"] if not seat["broken"] and not seat["blocked"]}
        kept, removed = [], []
        for assignment in current:
            reason = ""
            if assignment["seat_id"] not in usable:
                reason = "seat is no longer usable"
            elif assignment["enrollment_id"] in excluded:
                reason = "DR exam booking"
            elif assignment["enrollment_id"] not in eligible:
                student = students.get(assignment["enrollment_id"], {})
                reason = (f"{student['leaving']} on the registrar's roster" if student.get("leaving")
                          else "dropped from the roster" if student.get("dropped") else "no longer in the selected sections")
            elif mode != "update":
                reason = "reshuffled"
            if reason:
                removed.append({**assignment, "reason": reason})
            else:
                kept.append(assignment)
        seated = {assignment["enrollment_id"] for assignment in kept}
        rng = secrets.SystemRandom()
        to_seat = [key for key in eligible if key not in seated]
        rng.shuffle(to_seat)
        if limit is not None:
            to_seat = to_seat[:limit]
        kept_ids = {assignment["seat_id"] for assignment in kept}
        free = [seat_id for seat_id in usable if seat_id not in kept_ids]
        if not students:
            raise ValueError("The selected sections have no roster students. Import their rosters first.")
        if not eligible:
            raise ValueError("Every student in the selected sections has a DR exam booking for this exam, so there is nobody to seat.")
        if len(to_seat) > len(free):
            raise Conflict(
                f"{len(to_seat)} student{'s' if len(to_seat) != 1 else ''} need seats but {room['name']} has only {len(free)} usable "
                f"seat{'s' if len(free) != 1 else ''} free: {room['seat_count']} seats, {room['broken_count']} broken, "
                f"{room['blocked_count']} blocked{f', {len(kept)} already seated' if kept else ''}. "
                f"{len(excluded)} student{'s' if len(excluded) != 1 else ''} with DR exam bookings {'are' if len(excluded) != 1 else 'is'} not counted. "
                "Choose a larger room, seat fewer sections, or unblock seats.")
        placements = self._place(usable, kept_ids, free, len(to_seat), rng)
        for assignment in removed:
            db.execute("DELETE FROM seating_assignments WHERE chart_id = ? AND seat_id = ?", (chart["id"], assignment["seat_id"]))
        for enrollment_id, seat_id in zip(to_seat, placements):
            db.execute("INSERT INTO seating_assignments VALUES (?, ?, ?, ?, ?)",
                       (chart["id"], seat_id, eligible[enrollment_id]["section_id"], enrollment_id, when))
        self._record_card_changes(db, chart, {row["enrollment_id"]: row for row in current}, when)
        spread = self.spacing(room["seats"], kept_ids | set(placements))
        # Verify the result against the roster and bookings, independently of the placement above.
        listed = len(students) - len(excluded) + len(shown)
        check, checked = self._record_check(db, chart["id"], chart["assignment_id"], section_ids, listed, len(shown))
        counts = {"students": listed, "excluded_dr": len(shown), "seated": len(kept) + len(to_seat),
                  "free_seats": len(free) - len(to_seat), "usable_seats": len(usable),
                  "spaced": spread["spaced"], "adjacent_pairs": spread["adjacent_pairs"], "spaced_capacity": spread["spaced_capacity"],
                  "check_ok": check["ok"]}
        detail = {"mode": mode, "kept": len(kept), "newly_seated": len(to_seat), "removed": [
            {"enrollment_id": item["enrollment_id"], "seat_id": item["seat_id"], "reason": item["reason"]} for item in removed],
            "excluded_dr": sorted(shown), "assignment_id": chart["assignment_id"], "counts": counts, "actor": "workspace", "check": checked}
        self.record_event(db, chart["id"], mode, detail, when)
        db.execute("""UPDATE seating_charts SET counts_json = ?, check_json = ?, checked_at = ?, generated_at = ?, updated_at = ?, version = version + 1
                      WHERE id = ?""", (json.dumps(counts), json.dumps(checked), when, when, when, chart["id"]))

    @staticmethod
    def _require_room_ready(room):
        if room.get("review_required"):
            raise Conflict(f"{room['name']} needs a room review before exam seating: " + "; ".join(room["review_required"]) +
                           ". Check the room's source notes and seat conditions, then confirm the review in Room settings.")

    def create(self, data):
        """One room for a set of sections; the multi-room form is create_plan."""
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object.")
        plan = {key: value for key, value in data.items() if key not in ("room_id", "section_ids")}
        plan["allocations"] = [{"room_id": data.get("room_id"), "section_ids": data.get("section_ids")}]
        return self.create_plan(plan)["charts"][0]

    def create_plan(self, data):
        """Seat an exam across rooms: one chart per room, created together or not at all.

        `allocations` gives each room its own sections. `pool` ({room_ids, section_ids}) mixes the sections instead: their
        students are shuffled together and each room seats its share (`pool_shares`), every chart listing all the sections.
        """
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object.")
        count = version_count(data.get("exam_version_count"), optional=True)
        allow_unequal = unequal_counts(data.get("allow_unequal_versions", False))
        notes = data.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 280:
            raise ValueError("Notes must be 280 characters or fewer.")
        pool = data.get("pool")
        if pool is not None:
            if not isinstance(pool, dict) or "allocations" in data:
                raise ValueError("Either mix the sections across rooms or give each section its own room.")
            every_section, room_ids = pool.get("section_ids"), pool.get("room_ids")
            if not isinstance(every_section, list) or not every_section:
                raise ValueError("Choose at least one section to mix across the rooms.")
            if not isinstance(room_ids, list):
                raise ValueError("Choose at least two rooms to mix the sections across.")
            allocations = [{"room_id": room_id, "section_ids": every_section} for room_id in room_ids]
        else:
            allocations = data.get("allocations")
            if not isinstance(allocations, list) or not allocations or len(allocations) > 10 or any(
                    not isinstance(item, dict) or not isinstance(item.get("room_id"), str) or not isinstance(item.get("section_ids"), list) for item in allocations):
                raise ValueError("Choose a room for at least one section.")
            every_section = [section for item in allocations for section in item["section_ids"]]
            if any(not isinstance(section, str) for section in every_section):
                raise ValueError("Section identifiers must be text.")
            if len(set(every_section)) != len(every_section):
                raise ValueError("Each section can be assigned to one room.")
            if len({item["room_id"] for item in allocations}) != len(allocations):
                raise ValueError("List each room once, with all of its sections.")
        when = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            assignment, sections, term_id = self._validate_scope(db, data.get("assignment_id"), every_section)
            rooms = self._pool_rooms(db, room_ids) if pool is not None else None
            self._check_overlap(db, assignment["id"], every_section)
            moved = self._relink(db, assignment["id"], data.get("relink_assignment_ids"))
            pool_id, shares = "", [None] * len(allocations)
            if pool is not None:
                # The shares follow the DR links as relinked above.
                students = [key for key, value in self._students(db, term_id, sections).items() if not value["dropped"] and not value["leaving"]]
                _, excluded = self._dr(db, every_section, assignment["id"], students)
                pool_id, shares = str(uuid.uuid4()), self.pool_shares(rooms, len(students) - len(excluded))
            charts = []
            version_offset = 0
            for item, share in zip(allocations, shares):
                if not item["section_ids"]:
                    raise ValueError("Every room needs at least one section.")
                if not db.execute("SELECT 1 FROM rooms WHERE id = ?", (item["room_id"],)).fetchone():
                    raise ValueError("Choose a room with an uploaded seating chart.")
                chart_id = str(uuid.uuid4())
                db.execute("""INSERT INTO seating_charts (id, assignment_id, term_id, room_id, notes, allow_unequal_versions, pool_id, created_at, updated_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (chart_id, assignment["id"], term_id, item["room_id"], notes.strip(), int(allow_unequal), pool_id, when, when))
                for section_id in item["section_ids"]:
                    db.execute("INSERT INTO seating_chart_sections VALUES (?, ?)", (chart_id, section_id))
                if moved and not charts:
                    self.record_event(db, chart_id, "relink", {"moved": moved}, when)
                # A room that is too small raises here and rolls back every chart of the plan.
                self._seat(db, self._chart(db, chart_id), "regenerate", when, limit=share)
                charts.append(chart_id)
            if pool_id:
                for chart_id in charts:  # each room's check now sees the students the later rooms seated
                    self._recheck(db, chart_id, when)
            if count is not None:
                for chart_id in charts:
                    version_offset += self._assign_exam_versions(db, self._chart(db, chart_id), count, when, version_offset, allow_unequal)
            return {"charts": [self.get(chart_id, db) for chart_id in charts]}

    def _exam_version_room(self, db, chart):
        """Revalidate ownership and seating under the caller's write transaction."""
        sections = self._sections(db, chart["id"])
        section_ids = [section["id"] for section in sections]
        _, _, term_id = self._validate_scope(db, chart["assignment_id"], section_ids)
        if term_id != chart["term_id"]:
            raise Conflict("This chart's course term changed. Review its sections before assigning exam versions.")
        room = self.rooms.get(chart["room_id"], db)
        self._require_room_ready(room)
        if not self._check(db, chart["id"], chart["assignment_id"], section_ids)["ok"]:
            raise Conflict("Update seating to resolve this chart's roster and seat issues before assigning exam versions.")
        occupants = {row["seat_id"]: dict(row) for row in db.execute(
            "SELECT * FROM seating_assignments WHERE chart_id=?", (chart["id"],))}
        usable = {seat["id"] for seat in room["seats"] if not seat["broken"] and not seat["blocked"]}
        if not occupants:
            raise Conflict("Seat students on this chart before assigning exam versions.")
        if any(seat_id not in usable or occupant["section_id"] not in section_ids for seat_id, occupant in occupants.items()):
            raise Conflict("A student's seat or section has changed. Update seating before assigning exam versions.")
        return room, occupants

    def _assign_exam_versions(self, db, chart, count, when, offset=0, allow_unequal_versions=False):
        room, occupants = self._exam_version_room(db, chart)
        assigned = automatic_versions(room, occupants, count, offset, allow_unequal_versions)
        old = {row["seat_id"]: row["exam_version"] for row in db.execute(
            "SELECT seat_id,exam_version FROM seating_exam_versions WHERE chart_id=?", (chart["id"],))}
        if chart["exam_version_count"] == count and bool(chart["allow_unequal_versions"]) == allow_unequal_versions and old == assigned:
            return len(assigned)
        db.execute("DELETE FROM seating_exam_versions WHERE chart_id=?", (chart["id"],))
        db.executemany("INSERT INTO seating_exam_versions(chart_id,seat_id,exam_version) VALUES(?,?,?)",
                       [(chart["id"], seat_id, letter) for seat_id, letter in assigned.items()])
        db.execute("UPDATE seating_charts SET exam_version_count=?,allow_unequal_versions=?,updated_at=?,version=version+1 WHERE id=?",
                   (count, int(allow_unequal_versions), when, chart["id"]))
        self.record_event(db, chart["id"], "exam_versions_assigned",
                          {"count": count, "allow_unequal_versions": allow_unequal_versions, "seats": assigned}, when)
        return len(assigned)

    def assign_exam_versions(self, chart_id, version, count, allow_unequal_versions=False):
        count = version_count(count)
        allow_unequal_versions = unequal_counts(allow_unequal_versions)
        with self.rosters.lock():
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                chart = self._check_version(db, chart_id, version)
                self._assign_exam_versions(db, chart, count, timestamp(), allow_unequal_versions=allow_unequal_versions)
            return self.get(chart_id)

    def edit_exam_version(self, chart_id, data):
        seat_id = data.get("seat_id")
        target = data.get("target_seat_id")
        if not isinstance(seat_id, str) or not seat_id:
            raise ValueError("Choose an occupied seat to edit its exam version.")
        swap = "target_seat_id" in data
        if swap == ("exam_version" in data):
            raise ValueError("Choose another seat to swap versions, or choose one exam version.")
        if swap and (not isinstance(target, str) or not target):
            raise ValueError("Choose an occupied seat to swap exam versions.")
        with self.rosters.lock():
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                chart = self._check_version(db, chart_id, data.get("version"))
                count = chart["exam_version_count"]
                if count is None:
                    raise Conflict("Assign exam versions to this chart first.")
                _, occupants = self._exam_version_room(db, chart)
                if seat_id not in occupants or (swap and target not in occupants):
                    raise ValueError("Choose an occupied usable seat in this chart's room.")
                saved = {row["seat_id"]: row["exam_version"] for row in db.execute(
                    "SELECT seat_id,exam_version FROM seating_exam_versions WHERE chart_id=?", (chart_id,))}
                if swap:
                    if seat_id not in saved or target not in saved:
                        raise ValueError("Assign a version to both occupied seats before swapping their versions.")
                    changes = {seat_id: saved[target], target: saved[seat_id]}
                else:
                    letter = data.get("exam_version")
                    if not isinstance(letter, str) or len(letter) != 1 or not "A" <= letter < chr(65 + count):
                        raise ValueError(f"Choose an available exam version from A to {chr(64 + count)}.")
                    changes = {seat_id: letter}
                if any(saved.get(key) != letter for key, letter in changes.items()):
                    when = timestamp()
                    db.executemany("""INSERT INTO seating_exam_versions(chart_id,seat_id,exam_version) VALUES(?,?,?)
                        ON CONFLICT(chart_id,seat_id) DO UPDATE SET exam_version=excluded.exam_version""",
                        [(chart_id, key, letter) for key, letter in changes.items()])
                    db.execute("UPDATE seating_charts SET updated_at=?,version=version+1 WHERE id=?", (when, chart_id))
                    detail = {"mode": "swap" if swap else "set", "before": {key: saved.get(key) for key in changes}, "after": changes}
                    self.record_event(db, chart_id, "exam_version_changed", detail, when)
            return self.get(chart_id)

    def reseat(self, chart_id, version, mode):
        if mode not in ("regenerate", "update"):
            raise ValueError("Choose to update or regenerate the seating chart.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            chart = self._check_version(db, chart_id, version)
            self._seat(db, chart, mode, timestamp())
        self._revoke_changed_cards(chart_id)
        return self.get(chart_id)

    def move(self, chart_id, version, enrollment_id, seat_id):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._move(db, chart_id, version, enrollment_id, seat_id)
        self._revoke_changed_cards(chart_id)
        return self.get(chart_id)

    def _move(self, db, chart_id, version, enrollment_id, seat_id):
        """Apply a checked move inside the caller's lifecycle lock/transaction.

        Exam request resolution uses this same operation so its decision receipt
        and seat change commit together.
        """
        if not isinstance(enrollment_id, str) or not isinstance(seat_id, str):
            raise ValueError("Choose a student and a seat.")
        chart = self._check_version(db, chart_id, version)
        before = {row["enrollment_id"]: dict(row) for row in db.execute(
            "SELECT * FROM seating_assignments WHERE chart_id=?", (chart_id,))}
        self._require_room_ready(self.rooms.get(chart["room_id"], db, seats=False))
        seat = db.execute("SELECT * FROM room_seats WHERE id = ? AND room_id = ?", (seat_id, chart["room_id"])).fetchone()
        if seat is None:
            raise ValueError("That seat is not in this chart's room.")
        if seat["broken"] or seat["blocked"]:
            raise ValueError(f"Seat {seat['label']} is {'broken' if seat['broken'] else 'blocked'}. Choose another seat or change its condition in the room first.")
        mover = db.execute("SELECT * FROM seating_assignments WHERE chart_id = ? AND enrollment_id = ?", (chart_id, enrollment_id)).fetchone()
        occupant = db.execute("SELECT * FROM seating_assignments WHERE chart_id = ? AND seat_id = ?", (chart_id, seat_id)).fetchone()
        when = timestamp()
        if mover is None:
            sections = self._sections(db, chart_id)
            member = db.execute("""SELECT m.section_id FROM roster_current m
                JOIN seating_chart_sections x ON x.section_id=m.section_id
                JOIN course_sections s ON s.id=m.section_id
                WHERE x.chart_id=? AND m.enrollment_id=? AND s.term_id=? ORDER BY s.number DESC LIMIT 1""",
                (chart_id, enrollment_id, chart["term_id"])).fetchone()
            if member is None:
                raise ValueError("That student is not in the selected sections.")
            elsewhere = self._seated_elsewhere(db, chart_id).get(enrollment_id)
            if elsewhere:
                raise Conflict(f"That student is seated in {elsewhere}, another room of this exam. Move them there instead.")
            if enrollment_id in self.links.excluded(db, [section["id"] for section in sections], chart["assignment_id"]):
                raise Conflict("That student has a DR exam booking for this exam and cannot be seated.")
            if occupant is not None:
                raise Conflict(f"Seat {seat['label']} is taken. Choose a free seat for an unseated student.")
            db.execute("INSERT INTO seating_assignments VALUES (?, ?, ?, ?, ?)", (chart_id, seat_id, member["section_id"], enrollment_id, when))
        else:
            if occupant is not None and occupant["enrollment_id"] == enrollment_id:
                return
            if occupant is not None:
                source_seat = db.execute("SELECT broken,blocked FROM room_seats WHERE id=?", (mover["seat_id"],)).fetchone()
                if source_seat is None or source_seat["broken"] or source_seat["blocked"]:
                    raise Conflict("The student's current seat is no longer usable. Choose a free seat or update seating before swapping.")
            db.execute("DELETE FROM seating_assignments WHERE chart_id = ? AND enrollment_id = ?", (chart_id, enrollment_id))
            if occupant is not None:
                db.execute("UPDATE seating_assignments SET seat_id = ?, assigned_at = ? WHERE chart_id = ? AND enrollment_id = ?",
                           (mover["seat_id"], when, chart_id, occupant["enrollment_id"]))
            db.execute("INSERT INTO seating_assignments VALUES (?, ?, ?, ?, ?)", (chart_id, seat_id, mover["section_id"], enrollment_id, when))
        self.record_event(db, chart_id, "move", {"enrollment_id": enrollment_id, "seat_id": seat_id, "seat": seat["label"],
                                                 "swapped_with": occupant["enrollment_id"] if occupant else None}, when)
        db.execute("UPDATE seating_charts SET updated_at = ?, version = version + 1 WHERE id = ?", (when, chart_id))
        self._record_card_changes(db, chart, before, when)

    def _record_card_changes(self, db, chart, before, when):
        """Seat changes and obsolete-link intents commit with the same revision.

        Current notice revisions belong to this term-owned chart's permanent
        roster memberships. They never copy student names or change templates.
        """
        if not chart["confirmed_at"] or self.response_cards is None:
            return
        after = {row["enrollment_id"]: dict(row) for row in db.execute(
            "SELECT * FROM seating_assignments WHERE chart_id=?", (chart["id"],))}
        for enrollment_id in before.keys() | after.keys():
            old, new = before.get(enrollment_id), after.get(enrollment_id)
            if old and new and (old["seat_id"], old["section_id"]) == (new["seat_id"], new["section_id"]):
                continue
            membership = new or old
            db.execute("""INSERT INTO seating_card_updates
                (chart_id,enrollment_id,section_id,seat_id,created_at,updated_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(chart_id,enrollment_id) DO UPDATE SET
                    section_id=excluded.section_id,seat_id=excluded.seat_id,
                    revision=seating_card_updates.revision+1,card_id=NULL,updated_at=excluded.updated_at""",
                (chart["id"], enrollment_id, membership["section_id"], new["seat_id"] if new else None, when, when))
            # Include manually prepared or interrupted issues in this chart's
            # context, not just the issue IDs whose confirmation finished.
            card_ids = [row[0] for row in db.execute("""SELECT r.id FROM response_card_recipients r
                JOIN response_card_issues i ON i.id=r.issue_id
                WHERE i.seating_chart_id=? AND r.enrollment_id=?""", (chart["id"], enrollment_id))]
            self.response_cards.invalidate_cards(db, card_ids, reason="seat_changed")

    def _revoke_changed_cards(self, chart_id):
        if self.response_cards is None:
            return
        with connect(self.path) as db:
            ids = [row[0] for row in db.execute("""SELECT x.card_id FROM response_card_invalidations x
                JOIN response_card_recipients r ON r.id=x.card_id JOIN response_card_issues i ON i.id=r.issue_id
                WHERE i.seating_chart_id=? AND x.status!='complete'""", (chart_id,))]
        if ids:
            self.response_cards.reconcile_invalidations(ids, limit=len(ids))

    def initialize_card_updates(self):
        """Backfill already-moved confirmed seats without publishing or emailing."""
        if self.response_cards is None:
            return
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM seating_charts WHERE confirmed_at!=''").fetchall():
                chart = dict(row)
                recorded = (json.loads(chart["cards_json"] or "{}").get("seat") or {}).get("seats") or {}
                before = {r["enrollment_id"]: dict(r) for r in db.execute("""SELECT a.*,s.label FROM seating_assignments a
                    JOIN room_seats s ON s.id=a.seat_id WHERE a.chart_id=?""", (chart["id"],))}
                existing = {r[0] for r in db.execute("SELECT enrollment_id FROM seating_card_updates WHERE chart_id=?", (chart["id"],))}
                for enrollment_id, assignment in before.items():
                    if enrollment_id in existing:
                        continue
                    stale = enrollment_id in recorded and recorded[enrollment_id] != assignment["label"]
                    new = enrollment_id not in recorded and assignment["assigned_at"] > chart["confirmed_at"]
                    if stale or new:
                        assignment["seat_id"] = "previous-seat"
                self._record_card_changes(db, chart, before, timestamp())

    def validate_card(self, db, card, verify_source=True):
        """Validate a chart-context card before distributing its frozen link.

        Callers hold the roster lifecycle lock through publication/delivery.
        This also protects custom chart-context cards that are outside cards_json.
        """
        issue = db.execute("SELECT * FROM response_card_issues WHERE id=?", (card["issue_id"],)).fetchone()
        if issue is None or not issue["seating_chart_id"]:
            return False
        chart = self._chart(db, issue["seating_chart_id"])
        seat = db.execute("""SELECT s.*,a.section_id FROM seating_assignments a JOIN room_seats s ON s.id=a.seat_id
            WHERE a.chart_id=? AND a.enrollment_id=? AND a.section_id=?""",
            (chart["id"], card["enrollment_id"], card["section_id"])).fetchone()
        if not seat or seat["broken"] or seat["blocked"]:
            raise Conflict("This card no longer has a usable current seat. Prepare an updated seat card.")
        if card["enrollment_id"] in self.links.excluded(db, [card["section_id"]], chart["assignment_id"]):
            raise Conflict("This student now has a linked DR booking. Review their testing arrangement.")
        revision = db.execute("SELECT * FROM seating_card_updates WHERE chart_id=? AND enrollment_id=?",
                              (chart["id"], card["enrollment_id"])).fetchone()
        if revision:
            if revision["card_id"] != card["id"] or revision["seat_id"] != seat["id"]:
                raise Conflict("This card was superseded by a seat change. Prepare the current updated seat card.")
            if db.execute("""SELECT 1 FROM response_card_invalidations x JOIN response_card_recipients r ON r.id=x.card_id
                JOIN response_card_issues i ON i.id=r.issue_id WHERE i.seating_chart_id=? AND r.enrollment_id=?
                AND x.status!='complete' LIMIT 1""", (chart["id"], card["enrollment_id"])).fetchone():
                raise Conflict("The previous response card link must be deactivated before this update can be sent.")
        if not verify_source:
            return True
        # Compare personalized source values while normalizing the newly salted
        # UIN expectation. Raw UINs never enter these records or diagnostics.
        term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (chart["term_id"],)).fetchone())
        members = self.response_cards._students(db, term, self._sections(db, chart["id"]))
        student = next((item for item in members if item["section_id"] == card["section_id"] and item["enrollment_id"] == card["enrollment_id"]), None)
        if student is None or student["email"] != card["email"]:
            raise Conflict("This card's roster membership or email changed. Prepare a current card.")
        assignment = dict(db.execute("SELECT * FROM assignments WHERE id=?", (chart["assignment_id"],)).fetchone())
        room = self.rooms.get(chart["room_id"], db, seats=False)
        self._require_room_ready(room)
        source = db.execute("SELECT schema_json FROM response_card_versions WHERE id=?", (issue["version_id"],)).fetchone()
        variables = self.response_cards._variables(db, term, student, assignment, chart, dict(seat), room)
        context = json.loads(issue["context_json"])
        if context.get("exam_event_id") and hasattr(self.response_cards, "exam_workflow"):
            variables.update(self.response_cards.exam_workflow.issue_context(db, context, student))
        expected = personalize(json.loads(source["schema_json"]), variables)
        if _comparable(expected) != _comparable(json.loads(card["snapshot_json"])["schema"]):
            raise Conflict("The card's seat or exam details changed. Prepare an updated card before sending.")
        return True

    def _seat_update_states(self, db, chart_id, roster, states):
        """Orange clears only for a retained acceptance of this exact revision."""
        revisions = {row["enrollment_id"]: dict(row) for row in db.execute(
            "SELECT * FROM seating_card_updates WHERE chart_id=?", (chart_id,))}
        pending = []
        for student in roster:
            update = revisions.get(student["enrollment_id"])
            if not update or not student["seat_id"] or student["seat_id"] != update["seat_id"]:
                continue
            card = db.execute("SELECT * FROM response_card_recipients WHERE id=?", (update["card_id"],)).fetchone() if update["card_id"] else None
            # A card closed after its exam was sent all the same (RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM").
            accepted = card and (card["status"] != "revoked" or closed_statuses(db, [card["id"]])) and db.execute("""SELECT 1 FROM response_card_mail
                WHERE card_id=? AND kind='invitation' AND status='accepted' AND COALESCE(provider_receipt_json,'')!='' LIMIT 1""",
                (card["id"],)).fetchone()
            if card:
                state = self._card_state(db, "seat", dict(card), student, student["seat"])
                states[student["enrollment_id"]] = state
                student["card"] = state
            if accepted:
                continue
            revocations = [dict(row) for row in db.execute("""SELECT x.error FROM response_card_invalidations x
                JOIN response_card_recipients r ON r.id=x.card_id JOIN response_card_issues i ON i.id=r.issue_id
                WHERE i.seating_chart_id=? AND r.enrollment_id=? AND x.status!='complete'""", (chart_id, student["enrollment_id"]))]
            detail = [f"Seat updated to {student['seat']}. The updated response-card email has not been confirmed sent."]
            if revocations:
                detail.append("Deactivation of the previous link is pending; the replacement cannot be sent until it succeeds.")
                detail += list(dict.fromkeys(item["error"] for item in revocations if item["error"]))
            else:
                detail.append("Previous response card links have been deactivated.")
            if card:
                detail += state["detail"]
            else:
                detail.append("Use Send updated seat cards to prepare the new notice and email its link.")
            state = {**(states.get(student["enrollment_id"]) or {}), "kind": "seat",
                     "card_id": card["id"] if card else None, "issue_id": card["issue_id"] if card else None,
                     "state": "update_pending", "summary": "Seat update not sent", "detail": detail,
                     "problems": detail[:1], "delivery": card["delivery_status"] if card else "not_sent",
                     "status": card["status"] if card else "",
                     "link_available": bool(card and not revocations and state.get("link_available")),
                     "can_resend": bool(card and not revocations and state.get("can_resend"))}
            states[student["enrollment_id"]] = state
            student["card"] = state
            pending.append({**{key: student[key] for key in ("enrollment_id", "name", "section_number", "seat")},
                            "card_id": state["card_id"], "delivery": state["delivery"], "revocation_pending": bool(revocations)})
        return {"pending": len(pending), "students": pending}

    def update_settings(self, chart_id, version, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            chart = self._check_version(db, chart_id, version)
            notes = data.get("notes", chart["notes"])
            if not isinstance(notes, str) or len(notes) > 280:
                raise ValueError("Notes must be 280 characters or fewer.")
            when = timestamp()
            moved = self._relink(db, chart["assignment_id"], data.get("relink_assignment_ids"))
            if moved:
                self.record_event(db, chart_id, "relink", {"moved": moved}, when)
            db.execute("UPDATE seating_charts SET notes = ?, updated_at = ?, version = version + 1 WHERE id = ?", (notes.strip(), when, chart_id))
            return self.get(chart_id, db)

    def print_options(self, chart_id):
        """What confirming would print: attached exam versions, seated counts per section, current issues."""
        chart = self.get(chart_id)
        with connect(self.path) as db:
            versions = [dict(row) for row in db.execute(
                "SELECT id, label, page_count, created_at FROM assignment_versions WHERE assignment_id = ? ORDER BY created_at", (chart["assignment_id"],))]
        sections = [{"id": section["id"], "number": section["number"],
                     "seated": sum(1 for student in chart["students"] if student["seat"] and student["section_id"] == section["id"])}
                    for section in chart["section_details"]]
        dr_ready, dr_ambiguous = self._dr_notice_audience(chart)
        return {"versions": versions, "sections": sections, "issues": chart["issues"], "confirmed_at": chart["confirmed_at"],
                "confirmed_mode": chart["confirmed_mode"], "print_batches": chart["print_batches"], "title": chart["title"],
                "response_cards": {"available": self.response_cards is not None, "seated": sum(1 for s in chart["students"] if s["seat"]),
                                   "dr": len(dr_ready), "dr_ambiguous": [s["name"] for s in dr_ambiguous]}}

    def _dr_notice_audience(self, chart):
        """DR-booked students whose single linked booking can fill a notice, and those with several bookings."""
        dr_students = [student for student in chart["students"] if student["status"] == "dr_testing"]
        if not dr_students:
            return [], []
        section_ids = [section["id"] for section in chart["section_details"]]
        placeholders = ",".join("?" * len(section_ids))
        with connect(self.path) as db:
            counts = {row[0]: row[1] for row in db.execute(
                f"""SELECT b.enrollment_id, COUNT(*) FROM dr_exam_bookings b JOIN dr_exam_assignment_links l ON l.booking_id = b.id
                    WHERE l.assignment_id = ? AND b.section_id IN ({placeholders}) GROUP BY b.enrollment_id""", (chart["assignment_id"], *section_ids))}
        ready = [student for student in dr_students if counts.get(student["enrollment_id"]) == 1]
        ambiguous = [student for student in dr_students if counts.get(student["enrollment_id"], 0) != 1]
        return ready, ambiguous

    def card_templates(self, chart_id):
        """The two card templates to show side by side before confirming, with the exam time to fill in."""
        chart = self.get(chart_id)
        result = {"available": self.response_cards is not None, "configured": bool(self.response_cards and self.response_cards.remote.config.enabled),
                  "mail_configured": bool(self.response_cards and self.response_cards.mail.enabled),
                  "mail": self.response_cards.mail.status() if self.response_cards else {},
                  "exam_date": chart["exam_date"], "exam_time": chart["exam_time"], "title": chart["title"], "room": chart["room_name"]}
        if not chart["exam_date"] or not chart["exam_time"]:
            with connect(self.path) as db:
                sibling = db.execute("""SELECT exam_date, exam_time FROM seating_charts WHERE assignment_id = ? AND term_id = ? AND id != ?
                                        AND exam_date != '' ORDER BY updated_at DESC LIMIT 1""", (chart["assignment_id"], chart["term_id"], chart_id)).fetchone()
            if sibling:
                result["exam_date"], result["exam_time"] = result["exam_date"] or sibling[0], result["exam_time"] or sibling[1]
        dr_ready, dr_ambiguous = self._dr_notice_audience(chart)
        result["counts"] = {"seated": sum(1 for s in chart["students"] if s["seat"]), "dr": len(dr_ready), "dr_ambiguous": [s["name"] for s in dr_ambiguous]}
        for kind in ("seat", "dr"):
            schema = MANAGED_TEMPLATES[kind]["schema"]
            if self.response_cards:
                existing = [item for item in self.response_cards.templates(chart["definition_id"]) if item["name"] == MANAGED_TEMPLATES[kind]["name"]]
                if existing:
                    schema = self._managed_template(chart["definition_id"], kind)["schema"]
            result[kind] = with_uin_confirmation(json.loads(json.dumps(schema)))
        return result

    def _card_schemas(self, cards):
        """Validate the schemas edited in the confirmation window; None keeps the course templates."""
        if cards is None:
            return {}
        if not isinstance(cards, dict) or set(cards) - {"seat", "dr"}:
            raise ValueError("Cards must be an object with seat and dr templates.")
        return {kind: validate_schema(with_uin_confirmation(validate_schema(schema))) for kind, schema in cards.items()}

    def _managed_template(self, definition_id, kind, schema=None):
        spec = MANAGED_TEMPLATES[kind]
        existing = [item for item in self.response_cards.templates(definition_id) if item["name"] == spec["name"]]
        default = with_uin_confirmation(spec["schema"])
        wanted = with_uin_confirmation(schema) if schema is not None else default
        if existing:
            template = existing[0]
            stored = json.dumps(template["schema"], sort_keys=True)
            if schema is not None and stored != json.dumps(wanted, sort_keys=True):
                template = self.response_cards.save_template({"name": spec["name"], "definition_id": definition_id, "schema": wanted,
                                                              "version": template["version"]}, template["id"])
            elif schema is None and template["version"] == 1 and stored != json.dumps(default, sort_keys=True):
                # Never edited by the instructor: keep it in step with the current built-in wording.
                template = self.response_cards.save_template({"name": spec["name"], "definition_id": definition_id, "schema": default,
                                                              "version": template["version"]}, template["id"])
            elif schema is None and stored != json.dumps(with_uin_confirmation(template["schema"]), sort_keys=True):
                # Edited by the instructor: keep their wording, drop a check the rules no longer make (the DR location).
                template = self.response_cards.save_template({"name": spec["name"], "definition_id": definition_id,
                                                              "schema": with_uin_confirmation(template["schema"]),
                                                              "version": template["version"]}, template["id"])
            return template
        return self.response_cards.save_template({"name": spec["name"], "definition_id": definition_id, "schema": wanted})

    @staticmethod
    def _issue_ids(item):
        item = item or {}
        return [issue_id for issue_id in (item.get("issue_ids") or ([item["issue_id"]] if item.get("issue_id") else [])) if issue_id]

    def _current_cards(self, issue_ids, students, recorded_seats):
        """Latest card per student across this chart's issues: who still holds a current card, whose stale card to replace.

        Returns (current enrollment IDs, {enrollment_id: published stale card ID}).
        """
        if not issue_ids:
            return set(), {}
        placeholders = ",".join("?" * len(issue_ids))
        latest = {}
        with connect(self.path) as db:
            for row in db.execute(f"""SELECT id, enrollment_id, status, publish_status FROM response_card_recipients
                                      WHERE issue_id IN ({placeholders}) ORDER BY created_at, rowid""", issue_ids):
                latest[row["enrollment_id"]] = dict(row)
        current, replaced = set(), {}
        for student in students:
            card = latest.get(student["enrollment_id"])
            if not card or card["status"] == "revoked":
                continue
            recorded = recorded_seats.get(student["enrollment_id"]) if recorded_seats else None
            if recorded and student.get("seat") and recorded != student["seat"]:
                if card["publish_status"] == "published":
                    replaced[student["enrollment_id"]] = card["id"]
                continue
            current.add(student["enrollment_id"])
        return current, replaced

    def _prepare_cards(self, chart, request_id, schemas=None, publish=False, email=True, mail_provider="default", updates_only=False):
        """Prepare current notices under the same lifecycle lock as seat edits.

        Changed seats use a separate fixed, UIN-only template. Existing course
        templates and original snapshots remain unchanged. Partial preparation
        is retained before any public side effect so retries recover its cards.
        """
        with self.rosters.lock():
            current = self.get(chart["id"])
            if current["version"] != chart["version"]:
                raise Conflict(STALE)
            chart = current
            self._revoke_changed_cards(chart["id"])
            return self._prepare_cards_locked(chart, request_id, schemas, publish, email, mail_provider, updates_only)

    def _prepare_cards_locked(self, chart, request_id, schemas, publish, email, mail_provider, updates_only):
        schemas = schemas or {}
        previous = chart.get("cards") or {}
        result = {**json.loads(json.dumps(previous)), "prepared_at": timestamp(), "errors": [], "published": False, "emailed": False}
        result.setdefault("seat", None)
        result.setdefault("dr", None)
        section_ids = [section["id"] for section in chart["section_details"]]
        seated = [student for student in chart["students"] if student["seat"]]
        dr_ready, dr_ambiguous = self._dr_notice_audience(chart)
        mail = self.response_cards.mail
        with connect(self.path) as db:
            updates = {row["enrollment_id"]: dict(row) for row in db.execute(
                "SELECT * FROM seating_card_updates WHERE chart_id=? AND seat_id IS NOT NULL", (chart["id"],))}
            # A DR student of a mix holds one card across its rooms, even if the room listing them changed.
            others = [item for item in self._pool(db, chart["id"]) if item != chart["id"]]
            pooled_dr = [issue_id for row in db.execute(
                f"SELECT cards_json FROM seating_charts WHERE id IN ({','.join('?' * len(others))})", others)
                for issue_id in self._issue_ids(json.loads(row[0] or "{}").get("dr"))] if others else []

        def retain():
            with connect(self.path) as db:
                db.execute("UPDATE seating_charts SET cards_json=? WHERE id=?", (json.dumps(result), chart["id"]))

        for kind, students, context in (("seat", seated, {"seating_chart_id": chart["id"]}), ("dr", dr_ready, {"assignment_id": chart["assignment_id"]})):
            if updates_only and kind != "seat":
                continue
            prior = previous.get(kind) or {}
            issue_ids = list(self._issue_ids(prior))
            seats = dict(prior.get("seats") or {}) if kind == "seat" else None
            if not students and not issue_ids:
                continue
            label = "Seat assignment" if kind == "seat" else "DR testing"
            current, replaced = self._current_cards(issue_ids + (pooled_dr if kind == "dr" else []), students, seats or {})
            wanted = [student for student in students if student["enrollment_id"] not in current
                      and (not updates_only or student["enrollment_id"] in updates)]
            entry = {"issue_id": prior.get("issue_id"), "issue_ids": issue_ids, "recipients": 0, "kept": len(current),
                     "replaced": sum(student["enrollment_id"] in updates for student in wanted) if kind == "seat" else len(replaced),
                     "template_id": prior.get("template_id"), "version_id": prior.get("version_id")}
            if seats is not None:
                entry["seats"] = seats
            result[kind] = entry
            groups = [(kind, wanted)] if kind != "seat" else [
                ("seat", [student for student in wanted if student["enrollment_id"] not in updates]),
                *[("seat_update", [student]) for student in wanted if student["enrollment_id"] in updates]]
            for template_kind, audience in groups:
                if not audience:
                    continue
                try:
                    schema = MANAGED_TEMPLATES["seat_update"]["schema"] if template_kind == "seat_update" else schemas.get(kind)
                    template = self._managed_template(chart["definition_id"], template_kind, schema)
                    version = self.response_cards.freeze(template["id"], template["version"])
                    if template_kind == "seat_update":
                        # This key belongs to the exact seat revisions, rather
                        # than a browser click. Interrupted preparations reuse it.
                        revisions = sorted((student["enrollment_id"], updates[student["enrollment_id"]]["revision"], student["seat_id"])
                                           for student in audience)
                        key = "seat-update-" + hashlib.sha256(json.dumps([chart["id"], version["id"], revisions]).encode()).hexdigest()
                    else:
                        key = f"{request_id}:cards:{kind}"
                    issue = self.response_cards.issue({"request_id": key, "version_id": version["id"], "term_id": chart["term_id"],
                                                       "section_ids": section_ids, "policy": "once",
                                                       "enrollment_ids": sorted({student["enrollment_id"] for student in audience}), **context})
                    if issue["id"] not in issue_ids:
                        issue_ids.append(issue["id"])
                    entry.update({"issue_id": issue["id"], "template_id": template["id"], "version_id": version["id"]})
                    entry["recipients"] += issue["counts"].get("total", 0)
                    if seats is not None:
                        seats.update({student["enrollment_id"]: student["seat"] for student in audience})
                    if template_kind == "seat_update":
                        with connect(self.path) as db:
                            db.execute("BEGIN IMMEDIATE")
                            for recipient in issue["recipients"]:
                                update = updates[recipient["enrollment_id"]]
                                db.execute("""UPDATE seating_card_updates SET card_id=?,updated_at=?
                                    WHERE chart_id=? AND enrollment_id=? AND revision=? AND seat_id=?""",
                                    (recipient["id"], timestamp(), chart["id"], recipient["enrollment_id"], update["revision"], update["seat_id"]))
                            db.execute("UPDATE seating_charts SET cards_json=? WHERE id=?", (json.dumps(result), chart["id"]))
                    else:
                        retain()
                except (ValueError, Conflict, NotFound) as error:
                    result["errors"].append(f"{label} notices were not prepared: {getattr(error, 'description', None) or str(error)}")
            retain()
            if not publish:
                continue
            if updates_only:
                # A seat-update action cannot publish or email unrelated cards
                # from an earlier mixed initial issue.
                with connect(self.path) as db:
                    sending = [row[0] for row in db.execute("""SELECT DISTINCT r.issue_id FROM seating_card_updates u
                        JOIN response_card_recipients r ON r.id=u.card_id
                        WHERE u.chart_id=? AND u.seat_id IS NOT NULL""", (chart["id"],))]
            else:
                sending = issue_ids
            for issue_id in sending:
                with connect(self.path) as db:
                    active = db.execute("""SELECT 1 FROM response_card_recipients r WHERE issue_id=? AND status!='revoked'
                        AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id) LIMIT 1""", (issue_id,)).fetchone()
                if not active:
                    continue
                try:
                    published = self.response_cards.publish(issue_id)
                    entry["published"] = entry.get("published", 0) + published["counts"].get("published", 0)
                    result["published"] = True
                    if email and (mail.enabled or mail_provider == "gmail"):
                        queued = mail.queue_invitations(issue_id, replacements=updates if kind == "seat" else replaced, mail_provider=mail_provider)
                        if updates_only:
                            # A new explicit send action can retry a definite
                            # failure. Uncertain delivery needs its own reviewed
                            # resend action and is never retried by preparation.
                            with connect(self.path) as db:
                                rows = [dict(row) for row in db.execute("""SELECT r.* FROM response_card_recipients r
                                    JOIN seating_card_updates u ON u.card_id=r.id WHERE r.issue_id=? AND u.chart_id=?
                                    AND r.delivery_status IN ('failed','unconfirmed')""", (issue_id, chart["id"]))]
                            for recipient in rows:
                                if recipient["delivery_status"] == "unconfirmed":
                                    result["errors"].append(f"Delivery to {recipient['student_name']} is unconfirmed. Check with the student before using Resend email on their seat.")
                                else:
                                    mail.queue_resend(recipient["id"], mail_provider=mail_provider)
                                    queued["queued"] += 1
                        entry["emailed"] = entry.get("emailed", 0) + queued["queued"]
                        result["emailed"] = True
                        if queued["no_email"]:
                            result["errors"].append(f"{queued['no_email']} {label.lower()} notice recipients have no email address on the roster.")
                    elif email and not entry.get("mail_warning"):
                        entry["mail_warning"] = True
                        result["errors"].append(f"{label} notices were published but email sending is not configured, so no emails were sent.")
                except (ServiceUnavailable, Conflict, ValueError) as error:
                    result["errors"].append(f"{label} notices could not be published or emailed: {getattr(error, 'description', None) or str(error)}")
            retain()
        if not updates_only:
            for student in dr_ambiguous:
                result["errors"].append(f"{student['name']} has no single DR booking linked to {chart['title']}, so no DR notice was prepared; notify them directly.")
        retain()
        return result

    def send_seat_updates(self, chart_id, data):
        """Explicitly send only current seat-change notices; never create print jobs."""
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 120:
            raise ValueError("A request ID is required to send updated seat cards safely.")
        request_id = request_id.strip()
        provider = data.get("mail_provider", "default")
        command = {"version": data.get("version"), "mail_provider": provider}
        with self.rosters.lock():
            with connect(self.path) as db:
                for event in db.execute("SELECT detail_json FROM seating_events WHERE chart_id=? AND kind='seat_update_send' ORDER BY id DESC", (chart_id,)):
                    previous = json.loads(event[0])
                    if previous.get("request_id") != request_id:
                        continue
                    if previous["command"] != command:
                        raise Conflict("This request ID was already used for a different seat-update send.")
                    return {**self.get(chart_id, db), "seat_update_send": previous["receipt"]}
                self._check_version(db, chart_id, data.get("version"))
            if self.response_cards is None:
                raise Conflict("Response Cards are unavailable.")
            self.response_cards.mail._require_provider(provider)
            chart = self.get(chart_id)
            if not chart["confirmed_at"]:
                raise Conflict("Confirm this seating chart before sending seat updates.")
            self._require_room_ready(chart["room"])
            if not chart["seat_updates"]["pending"]:
                raise Conflict("There are no unsent seat updates in this chart.")
            cards = self._prepare_cards(chart, request_id, publish=True, email=True, mail_provider=provider, updates_only=True)
            receipt = {"queued": (cards.get("seat") or {}).get("emailed", 0), "errors": cards["errors"]}
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE seating_charts SET version=version+1,updated_at=? WHERE id=?", (timestamp(), chart_id))
                self.record_event(db, chart_id, "seat_update_send", {"request_id": request_id, "command": command, "receipt": receipt},
                                  timestamp())
                return {**self.get(chart_id, db), "seat_update_send": receipt}

    def _card_states(self, db, cards, roster):
        """Per-student response card state for the chart overlays: complete, pending, or issue, with what to show when clicked."""
        if not cards or not self.response_cards:
            return {}
        issues = {kind: self._issue_ids(cards.get(kind)) for kind in ("seat", "dr")}
        if not any(issues.values()):
            return {}
        recorded = (cards.get("seat") or {}).get("seats") or {}
        by_key = {}
        for kind, issue_ids in issues.items():
            if issue_ids:
                # Ordered oldest first, so a student's latest card (a replacement after a move) wins.
                for row in db.execute(f"SELECT * FROM response_card_recipients WHERE issue_id IN ({','.join('?' * len(issue_ids))}) ORDER BY created_at, rowid", issue_ids):
                    by_key[(kind, row["enrollment_id"])] = dict(row)
        # The per-card reads of `_card_state`, one read each for every card of these issues: each card's mail by kind (the latest
        # of a kind wins, in the per-card read's order), its accepted invitations, and its current submission.
        all_issues = [issue_id for ids in issues.values() for issue_id in ids]
        mails, copies = {}, {}
        for row in db.execute(f"""SELECT m.card_id, m.kind, m.status, m.error, m.updated_at FROM response_card_mail m
                                  JOIN response_card_recipients r ON r.id = m.card_id WHERE r.issue_id IN ({','.join('?' * len(all_issues))})
                                  ORDER BY m.created_at, m.rowid""", all_issues):
            mails.setdefault(row["card_id"], {})[row["kind"]] = {"kind": row["kind"], "status": row["status"], "error": row["error"], "updated_at": row["updated_at"]}
            if row["kind"] == "invitation" and row["status"] == "accepted":
                copies[row["card_id"]] = copies.get(row["card_id"], 0) + 1
        wanted = [card["current_submission_id"] for card in by_key.values() if card["current_submission_id"]]
        submissions = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            submissions.update((row["id"], row["submission_json"]) for row in db.execute(
                f"SELECT id, submission_json FROM response_card_submissions WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        facts = {"mails": mails, "copies": copies, "submissions": submissions,
                 "closed": closed_statuses(db, [card["id"] for card in by_key.values() if card["status"] == "revoked"])}
        states = {}
        for entry in roster:
            kind = "dr" if entry["status"] == "dr_testing" else "seat" if entry["seat"] else None
            if kind is None or not issues[kind]:
                continue
            card = by_key.get((kind, entry["enrollment_id"]))
            if card is None:
                problem = ("This student was seated after the cards were prepared. Re-confirm the chart to send them a card." if kind == "seat"
                           else "No DR card was prepared for this student: more than one booking is linked, or the booking came after the cards.")
                states[entry["enrollment_id"]] = {
                    "kind": kind, "card_id": None, "issue_id": issues[kind][-1], "state": "issue", "summary": "No response card", "status": "", "delivery": "",
                    "link_available": False, "can_resend": False, "detail": [problem], "problems": [problem]}
            else:
                states[entry["enrollment_id"]] = self._card_state(db, kind, card, entry, recorded.get(entry["enrollment_id"]), facts)
            entry["card"] = states[entry["enrollment_id"]]
        return states

    def _card_state(self, db, kind, card, entry, recorded_seat, facts=None):
        """One student's card state. `facts` holds the card's mail and submission as `_card_states` read them for a whole chart;
        without it they are read here."""
        local_time = recorded_time
        if facts is not None:
            mail, copies = facts["mails"].get(card["id"], {}), facts["copies"].get(card["id"], 0)
        else:
            mail = {row["kind"]: dict(row) for row in db.execute("SELECT kind, status, error, updated_at FROM response_card_mail WHERE card_id = ? ORDER BY created_at", (card["id"],))}
            copies = db.execute("SELECT COUNT(*) FROM response_card_mail WHERE card_id = ? AND kind = 'invitation' AND status = 'accepted'", (card["id"],)).fetchone()[0]
        status, delivery = card["status"], card["delivery_status"]
        if status == "revoked":
            # RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › THEY KEEP THEIR STATUS: closed at midnight after the exam, a card
            # shows what it was then; revoked earlier (a seat change, a Revoke), it stays a revoked card.
            status = (facts["closed"] if facts is not None else closed_statuses(db, [card["id"]])).get(card["id"], status)
        problems, lines = [], []
        stale = kind == "seat" and recorded_seat and recorded_seat != entry["seat"]
        if stale:
            problems.append(f"The card says seat {recorded_seat}, but this chart now has {entry['seat']}. Re-confirm the chart or tell the student.")
        if card["publish_status"] == "failed":
            problems.append(f"The card could not be published: {card['error'] or 'unknown error'}.")
        invitation = mail.get("invitation") or {}
        if delivery == "no_email":
            problems.append("No email address on the roster, so the student was not emailed. Share the link another way.")
        elif delivery == "failed":
            problems.append(f"The email could not be sent: {invitation.get('error') or 'rejected by the mail service'}")
        elif delivery == "unconfirmed":
            problems.append("Email delivery could not be confirmed. Check with the student before sending again.")
        elif delivery == "queued":
            lines.append("Email queued to send.")
        elif delivery == "sent":
            lines.append(f"Emailed {local_time(invitation.get('updated_at', ''))}." if copies <= 1
                         else f"Emailed {copies} times, most recently {local_time(invitation.get('updated_at', ''))}.")
        elif card["publish_status"] != "published":
            lines.append("Card prepared, not yet published.")
        submission = None
        if card["current_submission_id"]:
            if facts is not None:
                text = facts["submissions"].get(card["current_submission_id"])
                submission = unchecked_dropped(json.loads(text)) if text is not None else None
            else:
                row = db.execute("SELECT submission_json FROM response_card_submissions WHERE id = ?", (card["current_submission_id"],)).fetchone()
                submission = unchecked_dropped(json.loads(row["submission_json"])) if row else None
        if status == "revoked":
            problems.append("The card was revoked.")
        elif status == "expired":
            problems.append("The card expired without a response.")
        elif status == "needs_review":
            problems.append("The answers differ from our records:")
            problems.extend(self._mismatches(card, submission))
        if status in ("submitted", "verified", "needs_review"):
            if submission:
                lines.append(f"Submitted {local_time(submission.get('submitted_at', ''))}.")
            confirmation = mail.get("confirmation")
            if confirmation is None:
                lines.append("Confirmation email not yet queued.")
            elif confirmation["status"] == "accepted":
                lines.append("Confirmation emailed.")
            elif confirmation["status"] in ("pending", "sending"):
                lines.append("Confirmation email queued.")
            elif confirmation["status"] == "cancelled" and card["status"] == "revoked":
                lines.append("Confirmation email not sent: the card closed first.")  # its pending mail is cancelled at closing
            else:
                problems.append(f"The confirmation email could not be sent: {confirmation['error']}")
        elif status == "opened":
            lines.append("Opened, not yet submitted.")
        elif status == "started":
            lines.append("Started, not yet submitted.")
        elif status == "issued" and delivery == "sent":
            lines.append("Not yet opened.")
        if card["status"] == "revoked" and status != "revoked":
            lines.append("No response before the card closed at midnight after the exam." if status == "closed"
                         else "The card closed at midnight after the exam.")
        if problems:
            state, summary = "issue", "Answers need review" if status == "needs_review" else "Response card issue"
        elif status in ("submitted", "verified"):
            state, summary = "complete", "Completed response card"
        elif status == "closed":
            state, summary = "pending", "No response"
        else:
            state, summary = "pending", "Pending response card"
        # A stale card must be replaced by re-confirming, never re-sent; otherwise the instructor may email a pending card again.
        mailer = self.response_cards.mail
        return {"kind": kind, "card_id": card["id"], "issue_id": card["issue_id"], "state": state, "summary": summary,
                "status": status, "delivery": delivery, "detail": problems + lines, "problems": problems,
                "link_available": bool(not stale and not mailer.awaiting_response(db, card)),
                "can_resend": bool((mailer.enabled or mailer.gmail.connected) and not stale and not mailer.resend_block(db, card))}

    @staticmethod
    def _mismatches(card, submission):
        schema = json.loads(card["snapshot_json"])["schema"]
        answers, checks = (submission or {}).get("answers") or {}, (submission or {}).get("verification") or {}
        out = []
        for field in schema.get("fields", []):
            check = checks.get(field["id"])
            matched = check.get("matched") if isinstance(check, dict) else (None if check is None else bool(check))
            if matched is not False:
                continue
            verification = field.get("verification") or {}
            answer = answers.get(field["id"])
            shown = ", ".join(map(str, answer)) if isinstance(answer, list) else "Yes" if answer is True else "No" if answer is False else str(answer)
            label = field.get("label", field["id"]).rstrip(":")
            expected = verification.get("expected")
            if field.get("type") == "time":
                shown, expected = str(twelve_hour(shown)), twelve_hour(expected) if isinstance(expected, str) else expected
            out.append(f"{label}: answered “{shown}”" + (f"; records show “{expected}”" if isinstance(expected, str) and verification.get("mode") != "uin-digest" else "") + ".")
        return out or ["One or more answers were flagged by the response service."]

    def _card_counts(self, db, cards):
        """Totals over the chart's seat and DR notices: cards, responses and answers flagged for review (counts only)."""
        summary = self._cards_summary(db, cards)
        kinds = [summary.get(kind) for kind in ("seat", "dr") if isinstance(summary.get(kind), dict)]
        return {key: sum(int(item.get(key) or 0) for item in kinds) for key in ("recipients", "responded", "flagged")}

    def _cards_summary(self, db, cards):
        """Live publish counts for the issues a confirmation prepared."""
        if not cards or not self.response_cards:
            return cards or {}
        summary = dict(cards)
        for kind in ("seat", "dr"):
            item = summary.get(kind)
            issue_ids = self._issue_ids(item)
            if not item or not issue_ids:
                continue
            # Live counts over every issue this chart produced, ignoring revoked (replaced) cards; a card closed after its exam
            # counts as it stood when it closed (RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › THEY KEEP THEIR STATUS).
            rows = [dict(row) for row in db.execute(f"""SELECT id, enrollment_id, status, publish_status, delivery_status FROM response_card_recipients
                                                         WHERE issue_id IN ({','.join('?' * len(issue_ids))})""", issue_ids)]
            closed = closed_statuses(db, [row["id"] for row in rows if row["status"] == "revoked"])
            rows = [{**row, "status": closed.get(row["id"], row["status"])} for row in rows if row["status"] != "revoked" or row["id"] in closed]
            summary[kind] = {**{key: value for key, value in item.items() if key not in ("seats", "mail_warning")}, "issue_ids": issue_ids, "issue_id": issue_ids[-1],
                             "recipients": len({row["enrollment_id"] for row in rows}),
                             "published": sum(row["publish_status"] == "published" for row in rows),
                             "responded": sum(row["status"] in ("submitted", "verified", "needs_review") for row in rows),
                             "emailed": sum(row["delivery_status"] == "sent" for row in rows),
                             "flagged": sum(row["status"] == "needs_review" for row in rows),
                             "queued": sum(row["delivery_status"] == "queued" for row in rows)}
        return summary

    def confirm(self, chart_id, data):
        """Confirm the chart and export one print job per section, with the exam PDF or blank stamp pages.

        Unresolved roster issues must be acknowledged explicitly. Confirmation is
        recorded on the chart and moves its removal to the typed-confirmation tier.
        """
        if self.documents is None:
            raise Conflict("Printing is unavailable.")
        mode = data.get("mode", "print")
        if mode not in ("print", "stamps_only"):
            raise ValueError("Choose to print the exam PDF or export stamps only.")
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 120:
            raise ValueError("A request ID is required so a retry reuses the same print jobs.")
        extras = data.get("extras", 0)
        if type(extras) is not int or not 0 <= extras <= 100:
            raise ValueError("Extra unassigned copies per section must be between 0 and 100.")
        seat_marks = data.get("seat_marks", False)
        if seat_marks not in (True, False):
            raise ValueError("Say whether to print a visible seat label beside each stamp.")
        # One more peel-off label per copy, for the student's Scantron (DOCUMENTS "SCANTRON LABELS").
        sheet_labels = data.get("sheet_labels", False)
        if type(sheet_labels) is not bool:
            raise ValueError("Say whether to print a Scantron label for each student.")
        acknowledge = data.get("acknowledge_issues", False)
        if acknowledge not in (True, False):
            raise ValueError("Say whether to continue despite unresolved issues.")
        prepare_cards = data.get("prepare_cards", True)
        if prepare_cards not in (True, False):
            raise ValueError("Say whether to prepare response cards.")
        if prepare_cards and self.response_cards is None:
            raise Conflict("Response Cards are unavailable, so notices cannot be prepared.")
        publish_cards = data.get("publish_cards", True)
        if publish_cards not in (True, False):
            raise ValueError("Say whether to publish the response cards now.")
        email_cards = data.get("email_cards", True)
        if type(email_cards) is not bool:
            raise ValueError("Say whether Faculty should email the response cards now.")
        # This is a choice for these invitation jobs, not a chart or course preference.
        mail_provider = data.get("mail_provider", "default")
        if mail_provider not in ("default", "gmail"):
            raise ValueError("Choose the default email provider or the connected Gmail account.")
        if prepare_cards and publish_cards and email_cards:
            self.response_cards.mail.resolve_provider(mail_provider)
        chart = self.get(chart_id)
        self._require_room_ready(chart["room"])
        if type(data.get("version")) is not int or data["version"] != chart["version"]:
            raise Conflict(STALE)
        if chart["issues"] and not acknowledge:
            raise Conflict(f"{len(chart['issues'])} unresolved issue{'s' if len(chart['issues']) != 1 else ''}: " + " ".join(chart["issues"])
                           + " Resolve them with Update seating, or continue anyway.")
        with connect(self.path) as db:
            versions = [dict(row) for row in db.execute("SELECT * FROM assignment_versions WHERE assignment_id = ? ORDER BY created_at", (chart["assignment_id"],))]
        if mode == "print":
            version_id = data.get("version_id") or (versions[-1]["id"] if versions else None)
            if not version_id:
                raise Conflict(f"No exam PDF is attached to {chart['title']}. Add the paper in its assignment's Create, or export stamps only.")
            if version_id not in {item["id"] for item in versions}:
                raise ValueError("Choose a registered version of this exam.")
        else:
            version_id = self.documents.register_stamp_pages(chart["assignment_id"], data.get("page_count"))["id"]
        # The review window comes after the export choices, so its fields are checked last.
        exam_date, exam_time = data.get("exam_date", ""), data.get("exam_time", "")
        schemas = {}
        if prepare_cards:
            if not isinstance(exam_date, str) or not exam_date:
                raise ValueError("Enter the exam date so the seat notices can state it.")
            try:
                dt.date.fromisoformat(exam_date)
            except ValueError:
                raise ValueError("Enter the exam date as YYYY-MM-DD.") from None
            if not isinstance(exam_time, str) or not exam_time.strip() or len(exam_time) > 40:
                raise ValueError("Enter the exam time, for example 7:00 PM.")
            exam_time = " ".join(exam_time.split())
            schemas = self._card_schemas(data.get("cards"))
        if prepare_cards and (exam_date != chart["exam_date"] or exam_time != chart["exam_time"]):
            # The seat notices read the exam time from the chart, so record it before issuing.
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                self._check_version(db, chart_id, data["version"])
                db.execute("UPDATE seating_charts SET exam_date = ?, exam_time = ?, updated_at = ? WHERE id = ?", (exam_date, exam_time, timestamp(), chart_id))
        seated = {}
        for student in chart["students"]:
            if student["seat"]:
                seated[student["section_id"]] = seated.get(student["section_id"], 0) + 1
        batches = []
        for section in chart["section_details"]:
            if not seated.get(section["id"]):
                continue
            batch = self.documents.create_batch(version_id, section["id"], None, f"{request_id.strip()}:{section['id']}",
                                                seating_chart_id=chart_id, extras=extras, seat_marks=seat_marks, sheet_labels=sheet_labels)
            batches.append({"id": batch["id"], "section_number": section["number"], "quantity": batch["quantity"], "status": batch["status"]})
        if not batches:
            raise ValueError("Nobody is seated in this chart, so there is nothing to print.")
        # Skipping the cards on a later export keeps the record of the cards already sent.
        cards = self._prepare_cards(chart, request_id.strip(), schemas, publish_cards, email_cards, mail_provider) if prepare_cards else (chart.get("cards") or {})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_version(db, chart_id, data["version"])
            when = timestamp()
            db.execute("""UPDATE seating_charts SET confirmed_at = ?, confirmed_mode = ?, confirmed_version_id = ?, cards_json = ?, updated_at = ?,
                          version = version + 1 WHERE id = ?""", (when, mode, version_id, json.dumps(cards), when, chart_id))
            self.record_event(db, chart_id, "confirmed", {"mode": mode, "version_id": version_id, "batches": batches, "extras": extras,
                                                          "acknowledged_issues": chart["issues"] if acknowledge else [],
                                                          "response_cards": cards}, when)
            result = self.get(chart_id, db)
        return {**result, "exported_batches": batches}

    def room_export(self, chart_id, batch_ids, kind, media):
        """The whole room's print PDF or labels in seat order, cut from this chart's ready print jobs."""
        if self.documents is None:
            raise Conflict("Printing is unavailable.")
        chart = self.get(chart_id)
        known = {batch["id"] for batch in chart["print_batches"]}
        if not batch_ids or any(batch_id not in known for batch_id in batch_ids):
            raise ValueError("Choose print jobs that belong to this seating chart.")
        return self.documents.room_export(batch_ids, kind, media)

    def delete(self, chart_id, version, confirmation=None):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            chart = self._check_version(db, chart_id, version)
            jobs = self._print_jobs(db, chart["assignment_id"], chart["term_id"])
            if jobs or chart["confirmed_at"]:
                title = db.execute("SELECT title FROM assignments WHERE id = ?", (chart["assignment_id"],)).fetchone()[0]
                typed = confirmation.strip().casefold() if isinstance(confirmation, str) else ""
                if typed != "delete":
                    if jobs:
                        raise Conflict(f"{jobs} print job{'s' if jobs != 1 else ''} with Data Matrix page stamps already exist{'s' if jobs == 1 else ''} "
                                       f"for {title} this term. Type delete to confirm removing this seating chart.")
                    raise Conflict(f"This seating chart was confirmed for printing on {chart['confirmed_at'][:10]}. "
                                   "Type delete to confirm removing it.")
            db.execute("DELETE FROM seating_charts WHERE id = ?", (chart_id,))

    def get(self, chart_id, db=None):
        if db is None:
            with self.rosters.lock(), connect(self.path) as db:
                return self.get(chart_id, db)
        chart = next(iter(self.list(db, chart_id)), None)
        if chart is None:
            raise NotFound("This seating chart no longer exists.")
        sections, students, dr, excluded, shown = self._scope(db, chart)
        section_ids = [section["id"] for section in sections]
        room = self.rooms.get(chart["room_id"], db)
        seats = {seat["id"]: seat for seat in room["seats"]}
        assignments = {row["enrollment_id"]: dict(row) for row in db.execute("SELECT * FROM seating_assignments WHERE chart_id = ?", (chart_id,))}
        exam_versions = {row["seat_id"]: row["exam_version"] for row in db.execute(
            "SELECT seat_id,exam_version FROM seating_exam_versions WHERE chart_id=?", (chart_id,))}
        roster = []
        for enrollment_id, student in sorted(students.items(), key=lambda item: (item[1]["name"].casefold(), item[0])):
            assignment = assignments.get(enrollment_id)
            if (student["dropped"] or student["leaving"]) and not assignment:
                continue  # dropped and holding no seat here: not part of this chart any more
            if enrollment_id in excluded and enrollment_id not in shown and not assignment:
                continue  # a mix lists its DR students on its first chart
            seat = seats.get(assignment["seat_id"]) if assignment else None
            entry = {**student, "seat_id": seat["id"] if seat else None, "seat": seat["label"] if seat else None, "status": "seated"}
            if enrollment_id in excluded:
                entry["status"] = "dr_testing"
            elif not seat:
                entry["status"] = "unseated"
            elif seat["broken"] or seat["blocked"]:
                entry["status"] = "seat_unusable"
            entry["exam_version"] = exam_versions.get(seat["id"]) if seat and entry["status"] == "seated" else None
            roster.append(entry)
        card_states = self._card_states(db, chart.get("cards"), roster)
        seat_updates = self._seat_update_states(db, chart_id, roster, card_states)
        check = self._check(db, chart_id, chart["assignment_id"], section_ids)
        name_of = lambda enrollment_id: students.get(enrollment_id, {}).get("name") or f"Student {enrollment_id}"
        label_of = lambda seat_id: seats.get(seat_id, {}).get("label", "a removed seat")
        issues = [f"{name_of(item['enrollment_id'])} has a DR exam booking for {chart['title']} but is seated at {label_of(item['seat_id'])}." for item in check["dr_seated"]]
        issues += [f"{name_of(enrollment_id)} has no seat." for enrollment_id in check["missing_seat"]]
        issues += [f"{name_of(item['enrollment_id'])} is seated at {item['label']}, which is now {'broken' if item['broken'] else 'blocked'}." for item in check["seat_unusable"]]
        issues += [f"A student no longer in the selected sections is seated at {label_of(item['seat_id'])}." for item in check["not_in_roster"]]
        issues += [f"Room review: {text}" for text in check["room_review"]]
        check = {"ok": check["ok"], "counts": check["counts"]}
        spread = self.spacing(room["seats"], [item["seat_id"] for item in assignments.values()])
        by_seat = {}
        for item in assignments.values():
            by_seat.setdefault(item["seat_id"], item)  # the first, as a search of the assignments would find
        for seat in room["seats"]:
            seat["exam_version"] = exam_versions.get(seat["id"])
            assignment = by_seat.get(seat["id"])
            seat["student"] = None
            if assignment:
                student = students.get(assignment["enrollment_id"])
                seat["student"] = {"enrollment_id": assignment["enrollment_id"], "name": student["name"] if student else "Former student",
                                   "section_number": student["section_number"] if student else None,
                                   "dr_testing": assignment["enrollment_id"] in excluded, "crowded": seat["id"] in spread["crowded_ids"],
                                   "exam_version": exam_versions.get(seat["id"]),
                                   "card": card_states.get(assignment["enrollment_id"])}
        spread.pop("crowded_ids")
        notices = [f"{item['students']} student{'s' if item['students'] != 1 else ''} in these sections {'have' if item['students'] != 1 else 'has'} "
                   f"DR bookings linked to {item['title']}{' (created from the DR export)' if item['auto_created'] else ''}, not to {chart['title']}. "
                   "If that is the same exam, link those bookings in Chart settings." for item in dr["others"]]
        usable = [seat for seat in room["seats"] if not seat["broken"] and not seat["blocked"]]
        events = [{**dict(row), "detail": json.loads(row["detail_json"])} for row in db.execute(
            "SELECT id, kind, detail_json, created_at FROM seating_events WHERE chart_id = ? ORDER BY id DESC LIMIT 30", (chart_id,))]
        print_batches = [dict(row) for row in db.execute(
            """SELECT b.id, b.status, b.quantity, b.superseded_at, b.created_at, b.seat_marks, b.request_id,
                      EXISTS(SELECT 1 FROM print_batch_labels l WHERE l.print_batch_id = b.id) AS labels_ready,
                      EXISTS(SELECT 1 FROM sheet_labels t JOIN printed_copies c ON c.id = t.printed_copy_id WHERE c.print_batch_id = b.id) AS sheet_labels,
                      s.number AS section_number, v.label AS version_label
               FROM print_batches b JOIN course_sections s ON s.id = b.section_id JOIN assignment_versions v ON v.id = b.assignment_version_id
               WHERE b.seating_chart_id = ? ORDER BY b.created_at DESC""", (chart_id,))]
        for batch in print_batches:
            # One confirmation creates one job per section under "<request>:<section>"; the shared prefix groups a room's export.
            batch["export"] = batch.pop("request_id").rsplit(":", 1)[0]
        for event in events:
            event.pop("detail_json")
        return {**chart, "room": room, "students": roster, "issues": issues, "notices": notices, "events": events, "check": check,
                "exam_versions": exam_version_snapshot(room, roster, chart["exam_version_count"], exam_versions, check["ok"]),
                "print_batches": print_batches, "response_cards": self._cards_summary(db, chart.get("cards")), "seat_updates": seat_updates,
                "dr": dr, "dr_exam_label": chart["title"], "section_details": sections, "spacing": spread, "live_counts": {
                    "students": len(students) - len(excluded - shown), "excluded_dr": len(shown), "seated": sum(1 for item in roster if item["status"] == "seated"),
                    "unseated": sum(1 for item in roster if item["status"] == "unseated"), "usable_seats": len(usable),
                    "free_seats": len(usable) - sum(1 for seat in usable if seat["student"])}}


def register(app, service):
    api = Blueprint("seating", __name__, url_prefix="/api/seating")

    body = partial(json_object, "The request must be a JSON object.")

    @contextlib.contextmanager
    def stop_waits(what):
        """A restart waits for this request (canvas/stopping.py), and none starts once Faculty is stopping (503, nothing written).

        A Confirm issues, publishes and emails cards in steps; on 2026-10-15 a restart 4 s into one left HECC 207's seat cards
        issued and its DR cards never issued (SEATING "A RESTART WAITS")."""
        canvas = app.extensions.get("canvas")
        if canvas is None:
            yield
            return
        key = f"seating:{uuid.uuid4()}"
        canvas.writes.begin(key, what, "Faculty is restarting. Try again in a minute; nothing was changed.")
        try:
            yield
        finally:
            canvas.writes.end(key)

    @api.get("")
    def index():
        return jsonify(service.list())

    @api.get("/catalog")
    def catalog():
        return jsonify(service.catalog())

    @api.get("/options")
    def options():
        sections = [item for item in request.args.get("sections", "").split(",") if item]
        rooms = [item for item in request.args.get("rooms", "").split(",") if item]
        return jsonify(service.options(request.args.get("assignment_id", ""), sections, rooms or None))

    @api.get("/attachable")
    def attachable():
        sections = [item for item in request.args.get("sections", "").split(",") if item]
        return jsonify(service.attachable(request.args.get("definition_id", ""), sections, request.args.get("title", "")))

    @api.post("/attach")
    def attach():
        return jsonify(service.move_plan(body()))

    @api.post("")
    def create():
        return jsonify(service.create(body())), 201

    @api.post("/plan")
    def plan():
        return jsonify(service.create_plan(body())), 201

    @api.get("/<chart_id>")
    def detail(chart_id):
        return jsonify(service.get(chart_id))

    @api.patch("/<chart_id>")
    def settings(chart_id):
        data = body()
        return jsonify(service.update_settings(chart_id, data.get("version"), data))

    @api.delete("/<chart_id>")
    def delete(chart_id):
        data = body()
        service.delete(chart_id, data.get("version"), data.get("confirmation"))
        return "", 204

    @api.get("/<chart_id>/print-options")
    def print_options(chart_id):
        return jsonify(service.print_options(chart_id))

    @api.get("/<chart_id>/room-roster")
    def room_roster(chart_id):
        from seating_roster import room_roster
        return jsonify(room_roster(service, chart_id))

    @api.get("/<chart_id>/room-export/<kind>")
    def room_export(chart_id, kind):
        batch_ids = [item for item in request.args.get("batches", "").split(",") if item]
        media = request.args.get("media", DEFAULT_MEDIA)
        content = service.room_export(chart_id, batch_ids, kind, media)
        name = f"room-print-{chart_id}.pdf" if kind == "print" else f"room-{'scantron-' if kind == 'scantron-labels' else ''}labels-{media}-{chart_id}.pdf"
        return send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=name)

    @api.get("/<chart_id>/card-templates")
    def card_templates(chart_id):
        return jsonify(service.card_templates(chart_id))

    @api.get("/<chart_id>/mail-merge")
    def mail_merge(chart_id):
        return jsonify(SeatingMailMerge(service).preview(chart_id, request.args.get("audience", "unsent")))

    @api.get("/<chart_id>/gmail-send")
    def gmail_send_preview(chart_id):
        return jsonify(SeatingGmail(service).preview(chart_id, request.args.get("audience", "unsent")))

    @api.post("/<chart_id>/gmail-send")
    def gmail_send(chart_id):
        return jsonify(SeatingGmail(service).send(chart_id, body()))

    @api.get("/<chart_id>/mail-merge/download")
    def mail_merge_download(chart_id):
        content, name = SeatingMailMerge(service).download(chart_id, request.args.get("audience", "unsent"), request.args.get("fingerprint"))
        return send_file(io.BytesIO(content), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name=name, max_age=0)

    @api.post("/<chart_id>/confirm")
    def confirm(chart_id):
        data = body()
        with stop_waits("a seating chart's Confirm"):
            return jsonify(service.confirm(chart_id, data)), 201

    @api.post("/<chart_id>/seat-updates")
    def seat_updates(chart_id):
        data = body()
        with stop_waits("updated seat cards being sent"):
            return jsonify(service.send_seat_updates(chart_id, data))

    @api.post("/<chart_id>/exam-versions")
    def assign_exam_versions(chart_id):
        data = body()
        return jsonify(service.assign_exam_versions(chart_id, data.get("version"), data.get("count"), data.get("allow_unequal_versions", False)))

    @api.post("/<chart_id>/exam-versions/edit")
    def edit_exam_version(chart_id):
        return jsonify(service.edit_exam_version(chart_id, body()))

    @api.post("/<chart_id>/<mode>")
    def reseat(chart_id, mode):
        data = body()
        if mode == "move":
            return jsonify(service.move(chart_id, data.get("version"), data.get("enrollment_id"), data.get("seat_id")))
        return jsonify(service.reseat(chart_id, data.get("version"), mode))

    app.register_blueprint(api)
