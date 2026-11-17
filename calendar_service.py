"""Instructor-authored calendar events with explicit, permanent catalog owners.

Workspace events have no course owner; narrower events reference the existing
course definition, term, and section hierarchy. No payload directories or derived
navigation IDs are involved. Catalog backups retain these records, including
soft deletions and content-bound creation receipts. Dates are inclusive and times
remain campus-local wall-clock strings; this service performs no timezone shifts.
"""
from datetime import date as Date
import hashlib
import json
from pathlib import Path
import re
import uuid

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import decode_course, timestamp
from rosters import connect, term_key


FIELDS = ("title", "notes", "location", "date", "end_date", "time", "end_time", "all_day",
          "category", "scope", "definition_id", "term_id", "section_id")
SELECT = """SELECT e.*, d.code, t.term, t.term_other, t.year, s.number AS section_number
    FROM calendar_events e LEFT JOIN course_definitions d ON d.id=e.definition_id
    LEFT JOIN course_terms t ON t.id=e.term_id LEFT JOIN course_sections s ON s.id=e.section_id"""


def _date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    try:
        return Date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a valid date.") from error


def _text(value, label, maximum, *, required=False):
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValueError(f"{label} must contain {'1 to ' if required else 'at most '}{maximum} characters.")
    return value


def _uuid(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UUID.")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a UUID.") from error
    if str(parsed) != value.lower():
        raise ValueError(f"{label} must be a hyphenated UUID.")
    return str(parsed)


def _data(data, allowed):
    if not isinstance(data, dict):
        raise ValueError("Calendar event details must be an object.")
    if set(data) - set(allowed):
        raise ValueError("Calendar event details include an unsupported field.")


def _validated(data):
    result = {key: _text(data.get(key, ""), label, maximum, required=key == "title")
              for key, label, maximum in (("title", "Title", 160), ("notes", "Notes", 4000), ("location", "Location", 240))}
    start = _date(data.get("date"), "Date")
    end_value = data.get("end_date")
    end = _date(data.get("date") if end_value in (None, "") else end_value, "End date")
    if end < start:
        raise ValueError("End date cannot be earlier than the event date.")
    result.update(date=start.isoformat(), end_date=end.isoformat())
    all_day = data.get("all_day", True)
    if type(all_day) is not bool:
        raise ValueError("All day must be true or false.")
    result["all_day"] = all_day
    for key, label in (("time", "Time"), ("end_time", "End time")):
        value = data.get(key, "")
        if not isinstance(value, str) or (value and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value)):
            raise ValueError(f"{label} must be blank or use HH:MM in the campus's local time.")
        result[key] = value
    if all_day and (result["time"] or result["end_time"]):
        raise ValueError("All-day events cannot include times.")
    if not all_day and not result["time"]:
        raise ValueError("Choose a start time or mark the event as all day.")
    if end == start and result["end_time"] and result["end_time"] < result["time"]:
        raise ValueError("End time cannot be earlier than the start time on the same date.")
    result["category"] = data.get("category", "event")
    if result["category"] not in ("event", "deadline"):
        raise ValueError("Choose an event or a deadline.")
    result["scope"] = data.get("scope", "workspace")
    owners = {"workspace": (), "course": ("definition_id",), "term": ("definition_id", "term_id"),
              "section": ("definition_id", "term_id", "section_id")}
    if not isinstance(result["scope"], str) or result["scope"] not in owners:
        raise ValueError("Choose a workspace, course, term, or section scope.")
    for key in ("definition_id", "term_id", "section_id"):
        value = data.get(key)
        if value == "":
            value = None
        if key in owners[result["scope"]]:
            if not isinstance(value, str) or not value or len(value) > 100:
                raise ValueError(f"Choose the event's {key.removesuffix('_id').replace('_', ' ')}.")
        elif value is not None:
            raise ValueError("Calendar references must match the selected scope.")
        result[key] = value
    return result


def _term_label(term, other, year):
    return f"{other if term == 'Other' else term} {year}" if term else ""


def _record(row):
    result = dict(row)
    result.pop("request_id")
    result.pop("request_sha256")
    result["all_day"] = bool(result["all_day"])
    result["term_label"] = _term_label(result.pop("term"), result.pop("term_other"), result.pop("year"))
    result.update(source="custom", editable=True)
    return result


def protect_references(db, previous, replacement=None):
    """Keep a visible offering for each active event's precise owner scope.

    Saved offering identity edits create different permanent owners. They must
    not silently detach calendar events from their last offering; an instructor
    can explicitly change an event's scope, delete it, or archive the offering.
    The caller already holds the roster lifecycle lock and catalog transaction.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_events'").fetchone():
        return
    events = db.execute("""SELECT e.scope,e.definition_id,e.term_id,t.term,t.term_other,t.year,s.number
        FROM calendar_events e LEFT JOIN course_terms t ON t.id=e.term_id
        LEFT JOIN course_sections s ON s.id=e.section_id
        WHERE e.definition_id=? AND e.deleted_at IS NULL""", (previous["definition_id"],)).fetchall()
    if not events:
        return
    offerings = [decode_course(row) for row in db.execute(
        "SELECT * FROM courses WHERE deleted_at IS NULL AND id!=? AND definition_id=?",
        (previous["id"], previous["definition_id"]))]
    if replacement and replacement["definition_id"] == previous["definition_id"]:
        offerings.append(replacement)
    for event in events:
        covered = offerings
        if event["scope"] in ("term", "section"):
            covered = [offering for offering in covered if term_key(offering) == term_key(event)]
        if event["scope"] == "section":
            covered = [offering for offering in covered if event["number"] in offering["sections"]]
        if not covered:
            raise Conflict("This change would detach a calendar event. Keep its course, term and section, "
                           "change the event's scope first, or mark the course not current to archive it.")


class CalendarService:
    def __init__(self, rosters):
        self.rosters = rosters
        self.path = rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "calendar_migrations").glob("*.sql")):
                apply_sql(db, "calendar", migration.stem, migration.read_text(encoding="utf-8"))

    def catalog(self):
        with self.rosters.lock(), connect(self.path) as db:
            courses = [dict(row) for row in db.execute("SELECT id,code FROM course_definitions ORDER BY code COLLATE NOCASE,id")]
            terms = [{"id": row["id"], "definition_id": row["definition_id"],
                      "term_label": _term_label(row["term"], row["term_other"], row["year"])}
                     for row in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL ORDER BY year DESC,term,term_other,id")]
            sections = [dict(row) for row in db.execute("""SELECT s.id,s.term_id,s.number FROM course_sections s
                JOIN course_terms t ON t.id=s.term_id WHERE t.deleted_at IS NULL ORDER BY s.term_id,s.number,s.id""")]
            return {"courses": courses, "terms": terms, "sections": sections}

    def list(self, start, end):
        first, last = _date(start, "Range start"), _date(end, "Range end")
        if last < first or (last - first).days > 365:
            raise ValueError("Choose a calendar range of 1 to 366 days.")
        with self.rosters.lock(), connect(self.path) as db:
            return [_record(row) for row in db.execute(SELECT + """ WHERE e.deleted_at IS NULL
                AND e.date <= ? AND e.end_date >= ? ORDER BY e.date,e.time,e.title COLLATE NOCASE,e.id""", (end, start))]

    @staticmethod
    def _get(db, identifier, *, deleted=False):
        row = db.execute(SELECT + " WHERE e.id=?", (identifier,)).fetchone()
        if not row or (row["deleted_at"] and not deleted):
            raise NotFound("This calendar event no longer exists.")
        return _record(row)

    def get(self, identifier):
        with self.rosters.lock(), connect(self.path) as db:
            return self._get(db, identifier)

    @staticmethod
    def _owner(db, data):
        if data["definition_id"] and not db.execute("SELECT 1 FROM course_definitions WHERE id=?", (data["definition_id"],)).fetchone():
            raise ValueError("Choose an existing course for this event.")
        if data["term_id"] and not db.execute("""SELECT 1 FROM course_terms
                WHERE id=? AND definition_id=? AND deleted_at IS NULL""", (data["term_id"], data["definition_id"])).fetchone():
            raise ValueError("Choose a retained term of this course for this event.")
        if data["section_id"] and not db.execute("SELECT 1 FROM course_sections WHERE id=? AND term_id=?",
                                               (data["section_id"], data["term_id"])).fetchone():
            raise ValueError("Choose a section of this course term for this event.")

    @staticmethod
    def _version(record, value):
        if type(value) is not int or value < 1:
            raise ValueError("Calendar event version must be a positive integer.")
        if value != record["version"]:
            raise Conflict("This calendar event changed. Refresh it before saving or deleting it.")

    def create(self, data):
        _data(data, (*FIELDS, "request_id"))
        request_id = _uuid(data.get("request_id"), "Creation request ID")
        values = _validated(data)
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id,request_sha256,deleted_at FROM calendar_events WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["request_sha256"] != fingerprint:
                    raise Conflict("This creation request was already used for different calendar event details.")
                if previous["deleted_at"]:
                    raise Conflict("The event created by this request was deleted. Use a new request to create another event.")
                return self._get(db, previous["id"])
            self._owner(db, values)
            identifier, now = str(uuid.uuid4()), timestamp()
            columns = ("id", "request_id", "request_sha256", *FIELDS, "created_at", "updated_at")
            db.execute(f"INSERT INTO calendar_events ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                       (identifier, request_id, fingerprint, *(values[key] for key in FIELDS), now, now))
            return self._get(db, identifier)

    def update(self, identifier, data):
        _data(data, (*FIELDS, "version"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._get(db, identifier)
            self._version(previous, data.get("version"))
            values = _validated({**{key: previous[key] for key in FIELDS}, **data})
            self._owner(db, values)
            db.execute(f"UPDATE calendar_events SET {','.join(key + '=?' for key in FIELDS)},version=version+1,updated_at=? WHERE id=?",
                       (*(values[key] for key in FIELDS), timestamp(), identifier))
            return self._get(db, identifier)

    def delete(self, identifier, data):
        _data(data, ("version",))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._get(db, identifier)
            self._version(previous, data.get("version"))
            now = timestamp()
            db.execute("UPDATE calendar_events SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?", (now, now, identifier))
            return self._get(db, identifier, deleted=True)
