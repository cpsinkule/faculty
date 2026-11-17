"""SCHEDULING "CLASS SCHEDULE" (requirement, 2026-10-10).

A section's class schedule is a READ: its class days (the Term schedule's meetings while the term uses it, and the per-date class
events the Use left showing), each day's topics, and the work given or due that day. Every date another record holds is read from
its owner, never copied: the day, time and place a quiz or exam is given (`exam_workflow.given_dates.term_given`), a gradebook
column's due date, the due date of Faculty's Canvas copy for that section. Canvas itself is never contacted.

Stored here (migration `scheduling/003`, owner the course term) is only what no other record holds:
- PLANNED ROWS for work not created yet: {item, kind, title, sections ([] = every section), date, time, place, due_date,
  section_values {section: {date, time, place, due_date}}, note}. One set of rows for the term, a section's own value where it
  differs (the instructor: "each section has its own final exam time/date/place … regular exams are at the same time, but
  different places for each section"). A quiz, exam or final is GIVEN on `date` (and due on `due_date` when that is another day);
  homework is DUE on `date`; anything else is simply on `date`.
- TOPICS per class day: {date: {notes [notes section numbers], note, sections {section: {notes, note}}}}, the term's by date with
  a section's own entry where it differs. A notes section is named by its number and resolved at each read
  (`course_notes.service.current_sections`).
- LINKS: the real record a planned row became (a course-wide assignment or a gradebook column). A linked row FOLLOWS that record:
  the schedule shows the record's date, time and place, and the planned value only where the record has none.

For one section, each of an assessment's day, time and place comes from the most specific record that has it: its seating chart,
then the planned row's own value for that section, then the exam record's main sitting, then the planned row's value for every
section. So a single "Given on" typed for a final never replaces the four sections' own planned finals.

DISAGREEMENTS (`differs`; campus dates compared as written) are marked, never settled here: each is fixed where its date lives.
- gradebook_canvas: a column's due date and the section's due date in Faculty's Canvas copy differ, and the column does not carry
  the LATEST of its sections' Canvas dates (CANVAS "WEBASSIGN DUE DATES": a column has one date, the latest of its courses').
- given_gradebook / given_canvas: the day it is given differs from that due date, and the due date is not the latest section's
  given day. Not marked for work whose planned row records a due date apart from its given day.
- plan_given: a section's own planned day differs from a main sitting dated for every section, and no seating chart names it.
Certified work stays listed and is never marked (CERTIFIED-ARCHIVE: no attention).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect

from . import clock
from .service import ScheduleService, _day, _has, _legacy, current_use, encoded, occurrences, request_digest

KINDS = ("quiz", "exam", "final", "homework", "other")
GIVEN = ("quiz", "exam", "final")  # given on a day, perhaps due on another; homework is due on its day, anything else is on it
MAX_ITEMS, MAX_TOPIC_DAYS, MAX_NOTES = 150, 200, 8
TITLE, PLACE, NOTE, TIME = 120, 80, 200, 40
SLACK = 31  # days a planned date may fall outside the term's classes (a final after the last class day)
NUMBER = re.compile(r"\d{1,2}\.\d{1,2}")
VALUES = ("date", "time", "place", "due_date")
BEFORE, AFTER = 14, 28  # the window a read without dates answers: two weeks back, four ahead
RULES = ("gradebook_canvas", "given_gradebook", "given_canvas", "plan_given")
BLANK = {"revision": 0, "items": [], "topics": {}, "actor": "", "updated_at": ""}


# -- The stored plan ----------------------------------------------------------------------------------------------------------------
def _links(db, term_id):
    """{planned row: {assignment_id} or {gradebook_item}} from each row's latest link (an unlink leaves none)."""
    found = {}
    for row in db.execute("""SELECT item_id, action, assignment_id, gradebook_item FROM scheduling_plan_links l WHERE term_id=? AND rowid=(
            SELECT MAX(x.rowid) FROM scheduling_plan_links x WHERE x.term_id=l.term_id AND x.item_id=l.item_id)""", (term_id,)):
        if row[1] == "link":
            found[row[0]] = {"assignment_id": row[2]} if row[2] else {"gradebook_item": row[3]}
    return found


def plan(db, term_id):
    """The term's latest plan {revision, items, topics, actor, updated_at}, revision 0 before the first save. Each planned row
    carries `link`: the record it follows ({assignment_id} or {gradebook_item}), or None."""
    if not _has(db, "scheduling_plan_revisions"):
        return dict(BLANK)
    row = db.execute("""SELECT revision, items_json, topics_json, actor, created_at FROM scheduling_plan_revisions WHERE term_id=?
        ORDER BY revision DESC LIMIT 1""", (term_id,)).fetchone()
    if not row:
        return dict(BLANK)
    links = _links(db, term_id)
    return {"revision": row[0], "items": [{**item, "link": links.get(item["item"])} for item in json.loads(row[1])],
            "topics": json.loads(row[2]), "actor": row[3], "updated_at": row[4]}


def named_sections(db, term_id):
    """The section IDs the term's latest plan names (a row's sections and own values, a topic's own entries)."""
    found = plan(db, term_id)
    named = {section for item in found["items"] for section in list(item["sections"]) + list(item["section_values"])}
    return named | {section for entry in found["topics"].values() for section in entry.get("sections", {})}


def _text(value, limit, what):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Write {what} as text.")
    text = " ".join((value or "").split())
    if len(text) > limit:
        raise ValueError(f"Keep {what} to {limit} characters.")
    return text


def _date(value, bounds, what):
    if value in (None, ""):
        return ""
    day = _day(value)
    if not day:
        raise ValueError(f"Give {what} as YYYY-MM-DD.")
    if bounds and not bounds[0] <= day <= bounds[1]:
        raise ValueError(f"Keep {what} within a month of the term's classes ({clock.day_label(dt.date.fromisoformat(bounds[2]))} to "
                         f"{clock.day_label(dt.date.fromisoformat(bounds[3]))}).")
    return day


def _time(value):
    text = _text(value, TIME, "a time")
    if text and clock.parse_span(text) is None:
        raise ValueError('Write a time as a clock or a range, such as "7:05 PM" or "9:00 AM–10:15 AM".')
    return text


def _values(raw, bounds, kind):
    """A row's own or a section's {date, time, place, due_date}, checked; a due date apart from the day belongs to given work."""
    found = {"date": _date(raw.get("date"), bounds, "a date"), "time": _time(raw.get("time")),
             "place": _text(raw.get("place"), PLACE, "a place"), "due_date": _date(raw.get("due_date"), bounds, "a due date")}
    if found["due_date"] and kind not in GIVEN:
        raise ValueError("Only a quiz or an exam has a due date apart from its day; give homework its due date as its date.")
    return found


def _bounds(db, term_id):
    found = ScheduleService.revision(db, term_id)
    if not found["revision"]:
        return None
    first, last = dt.date.fromisoformat(found["first_day"]), dt.date.fromisoformat(found["last_day"])
    return ((first - dt.timedelta(days=SLACK)).isoformat(), (last + dt.timedelta(days=SLACK)).isoformat(), found["first_day"], found["last_day"])


def _row(raw, sections, bounds):
    """One planned row as sent, checked and in canonical form (its `item` as sent, None for a new row)."""
    if not isinstance(raw, dict) or set(raw) - {"item", "kind", "title", "sections", "section_values", "note", "link", *VALUES}:
        raise ValueError("Give each planned row its kind, title, sections, date, time and place.")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError("A planned row is a quiz, an exam, a final, homework or something else.")
    title = _text(raw.get("title"), TITLE, "a title")
    if not title:
        raise ValueError("Give each planned row a title.")
    named = raw.get("sections", [])
    if (not isinstance(named, list) or any(not isinstance(section, str) or section not in sections for section in named)
            or len(set(named)) != len(named)):
        raise ValueError("A planned row names sections of this term, each once (none for every section).")
    named = [] if set(named) == set(sections) else sorted(named, key=lambda section: _number(sections[section]))
    own = raw.get("section_values", {})
    if not isinstance(own, dict) or any(section not in sections or not isinstance(values, dict) or set(values) - set(VALUES)
                                        for section, values in own.items()):
        raise ValueError("Give a section's own date, time and place under that section.")
    if any(named and section not in named for section in own):
        raise ValueError("A section's own values belong to a section the row is for.")
    shared = _values(raw, bounds, kind)
    values = {}
    for section in sorted(own, key=lambda section: _number(sections[section])):
        mine = {key: value for key, value in _values(own[section], bounds, kind).items() if value and value != shared[key]}
        if mine:
            values[section] = mine
    return {"item": raw.get("item"), "kind": kind, "title": title, "sections": named, **shared, "section_values": values,
            "note": _text(raw.get("note"), NOTE, "a note")}


def _number(value):
    return (int(value) if str(value).isdigit() else 10 ** 9, str(value))


def _first_date(item):
    days = [item["date"], item["due_date"]] + [values.get(key, "") for values in item["section_values"].values() for key in ("date", "due_date")]
    return min((day for day in days if day), default="9999-99-99")


def _notes(raw, what):
    if not isinstance(raw, dict) or set(raw) - {"notes", "note", "sections"}:
        raise ValueError(f"Give {what} its notes sections and a note.")
    numbers = raw.get("notes", [])
    if (not isinstance(numbers, list) or len(numbers) > MAX_NOTES
            or any(not isinstance(number, str) or not NUMBER.fullmatch(number) for number in numbers)
            or len(set(numbers)) != len(numbers)):
        raise ValueError(f"Name at most {MAX_NOTES} notes sections for {what} by number, such as 2.3, each once.")
    return {"notes": list(numbers), "note": _text(raw.get("note"), NOTE, "a note")}


def _topic(raw, sections, day):
    """One class day's topics as sent, checked; None when it says nothing (the day then has no entry)."""
    what = f"the topics of {day}"
    entry = _notes(raw, what)
    own = raw.get("sections", {})
    if not isinstance(own, dict) or any(section not in sections for section in own):
        raise ValueError("Give a section's own topics under a section of this term.")
    mine = {}
    for section in sorted(own, key=lambda section: _number(sections[section])):
        if not isinstance(own[section], dict) or "sections" in own[section]:
            raise ValueError("Give a section's own topics as notes sections and a note.")
        found = _notes(own[section], what)
        if (found["notes"] or found["note"]) and found != entry:
            mine[section] = found
    if not entry["notes"] and not entry["note"] and not mine:
        return None
    return {**entry, "sections": mine}


def _sent(data, preview=False):
    """The change as sent: (rows to set, items to remove, topics by date). A preview has no request ID."""
    allowed = {"request_id", "base_revision", "set", "remove", "topics", "fingerprint"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Send request_id, base_revision and the rows to set or remove, or the topics to change.")
    request_id = data.get("request_id")
    if not preview:
        valid_request_id(request_id)
    if type(data.get("base_revision")) is not int:
        raise ValueError("Name the revision of the class schedule you edited as base_revision (0 for none).")
    rows, gone, topics = data.get("set", []), data.get("remove", []), data.get("topics", {})
    if not isinstance(rows, list) or not isinstance(gone, list) or not isinstance(topics, dict):
        raise ValueError("Send the rows to set and remove as lists and the topics by date.")
    return rows, gone, topics


def _fingerprint(term_id, data):
    """What a preview checked: this change against this revision (an Approve card sends it back; ASSISTANT previews)."""
    return request_digest({"term": term_id, "base_revision": data["base_revision"], "set": data.get("set", []),
                           "remove": data.get("remove", []), "topics": data.get("topics", {})})


def _change(db, term_id, data, rows, gone, topics):
    """The plan this change leaves, checked against the revision it names: (current plan, kept rows by item, new rows, topics).
    Writes nothing."""
    current = plan(db, term_id)
    if data["base_revision"] != current["revision"]:
        raise Conflict("This class schedule changed elsewhere. Read it again before saving.")
    sections = {section["id"]: section["number"] for section in ScheduleService._sections(db, term_id)}
    bounds = _bounds(db, term_id)
    items = {item["item"]: {key: value for key, value in item.items() if key != "link"} for item in current["items"]}
    for ident in gone:
        if not isinstance(ident, str) or items.pop(ident, None) is None:
            raise ValueError("A row to remove is not one of this term's planned rows. Read the class schedule again.")
    new, seen = [], set()
    for raw in rows:
        row = _row(raw, sections, bounds)
        if row["item"] is None:
            new.append(row)
        elif not isinstance(row["item"], str) or row["item"] not in items or row["item"] in seen:
            raise ValueError("A planned row is not one of this term's (or is sent twice). Read the class schedule again.")
        else:
            seen.add(row["item"])
            items[row["item"]] = row
    if len(items) + len(new) > MAX_ITEMS:
        raise ValueError(f"A class schedule has at most {MAX_ITEMS} planned rows.")
    titles = {}
    for row in list(items.values()) + new:
        other = titles.setdefault(row["title"].casefold(), row)
        if other is not row:
            # Name the clash (2026-10-28: a follow-up planned a second "In-class quiz" beside the Oct 16 one and failed on Approve).
            on = f" (already planned for {other['date']})" if other["date"] else ""
            raise ValueError(f"Two planned rows share a title: \"{row['title']}\"{on}. Give each its own, such as Quiz 6.")
    days = dict(current["topics"])
    for day, raw in topics.items():
        if _date(day, bounds, "a class day") != day:
            raise ValueError("Give each day of topics as YYYY-MM-DD.")
        entry = None if raw is None else _topic(raw, sections, day)
        if entry is None:
            days.pop(day, None)
        else:
            days[day] = entry
    if len(days) > MAX_TOPIC_DAYS:
        raise ValueError(f"A class schedule keeps topics for at most {MAX_TOPIC_DAYS} days.")
    return current, items, new, dict(sorted(days.items()))


def preview(service, term_id, data):
    """What a change would leave, checked exactly as `save` checks it, writing nothing: the rows it adds or changes, the titles it
    removes, and a `fingerprint` an Approve card sends back (ASSISTANT: a card the plan would refuse is never made)."""
    rows, gone, topics = _sent(data, preview=True)
    with connect(service.path) as db:
        db.execute("BEGIN")
        ScheduleService._term(db, term_id)
        current, items, new, days = _change(db, term_id, data, rows, gone, topics)
        before = {item["item"]: item for item in current["items"]}
        shown = ("kind", "title", "sections", "date", "time", "place", "due_date")
        changed = [{key: row[key] for key in shown} for row in sorted(list(items.values()) + new, key=_order)
                   if row["item"] is None or {k: v for k, v in before[row["item"]].items() if k != "link"} != row]
        return {"term_id": term_id, "base_revision": current["revision"], "set": changed,
                "remove": [before[ident]["title"] for ident in gone], "topics": sorted(topics),
                "fingerprint": _fingerprint(term_id, data)}


def save(service, term_id, data, actor="workspace"):
    """Change the term's plan against the revision the page showed (`base_revision`, 0 for none): `set` rows (one with `item`
    replaces that row, one without is new and gets its identity here), `remove` rows, and `topics` by date (None takes a day's
    entry away). One new whole revision; a change that leaves the plan as it was records nothing. Exact request IDs. A
    `fingerprint` (an Approve card's, from `preview`) must be this change's."""
    rows, gone, topics = _sent(data)
    request_id = data["request_id"]
    if "fingerprint" in data and data["fingerprint"] != _fingerprint(term_id, data):
        raise Conflict("This change is not the one previewed. Review it again.")
    digest = request_digest({"term": term_id, "set": rows, "remove": gone, "topics": topics})
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        ScheduleService._term(db, term_id)
        earlier = db.execute("SELECT request_sha256 FROM scheduling_plan_revisions WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier:
            if earlier[0] != digest:
                raise Conflict("This request ID already saved a different change to the class schedule.")
            return read(db, term_id)
        current, items, new, days = _change(db, term_id, data, rows, gone, topics)
        if new or sorted(items.values(), key=_order) != [{k: v for k, v in item.items() if k != "link"} for item in current["items"]] \
                or days != current["topics"]:
            now = timestamp()
            for row in new:
                row["item"] = uuid.uuid4().hex[:16]
                db.execute("INSERT INTO scheduling_plan_items(id, term_id, created_at) VALUES (?,?,?)", (row["item"], term_id, now))
            stored = sorted(list(items.values()) + new, key=_order)
            db.execute("""INSERT INTO scheduling_plan_revisions(term_id, revision, items_json, topics_json, request_id, request_sha256,
                actor, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                       (term_id, current["revision"] + 1, encoded(stored), encoded(days), request_id, digest, actor, now))
        db.commit()
        return read(db, term_id)


def _order(item):
    return (_first_date(item), item["title"].casefold(), item["item"] or "")


def page(db, section_id):
    """What a section's Class schedule page reads at once: its whole schedule, the term's sections, and the plan behind it (the
    rows as stored with their links, the topics as stored, and the rows no record follows yet with a likely match)."""
    found = db.execute("SELECT term_id FROM course_sections WHERE id=?", (str(section_id),)).fetchone()
    if not found:
        raise NotFound("That section was not found.")
    memo = TermRead(db, found[0])
    return {**section_schedule(db, section_id, term=memo, whole=True), "sections": ScheduleService._sections(db, memo.term_id),
            "definition_id": memo.term["definition_id"],
            "plan": {"items": memo.plan["items"], "topics": memo.plan["topics"], "open": open_items(db, memo.term_id, memo=memo)}}


def read(db, term_id):
    """The plan as its editor reads it: the rows with their links, the topics, the term's sections and its Term schedule's dates."""
    term = ScheduleService._term(db, term_id)
    found, schedule = plan(db, term_id), ScheduleService.revision(db, term_id)
    return {"term_id": term_id, "code": term["code"], "definition_id": term["definition_id"], "sections": ScheduleService._sections(db, term_id),
            **found, "term_schedule": {"revision": schedule["revision"], "in_use": bool(current_use(db, term_id)),
                                       "first_day": schedule["first_day"], "last_day": schedule["last_day"]}}


def link(service, term_id, data, actor="workspace"):
    """Record which real record a planned row became (`action` "link" with `assignment_id` or `gradebook_item`), or that it follows
    none ("unlink"). One record follows one row; the latest link of a row counts. Exact request IDs. `service` is any catalog
    service (its `rosters` lock and `path`): the quiz approval's worker links the row its quiz stands for."""
    if not isinstance(data, dict) or set(data) - {"request_id", "item", "action", "assignment_id", "gradebook_item"}:
        raise ValueError("Send request_id, the planned row and the record it follows.")
    request_id, action = data.get("request_id"), data.get("action", "link")
    valid_request_id(request_id)
    assignment, column = data.get("assignment_id"), data.get("gradebook_item")
    if action not in ("link", "unlink") or (action == "link") != (bool(assignment) != bool(column)) or (action == "unlink" and (assignment or column)):
        raise ValueError("Link a planned row to one assignment or one gradebook column, or unlink it.")
    digest = request_digest({"term": term_id, "item": data.get("item"), "action": action, "assignment_id": assignment,
                             "gradebook_item": column})
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        term = ScheduleService._term(db, term_id)
        earlier = db.execute("SELECT request_sha256 FROM scheduling_plan_links WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier:
            if earlier[0] != digest:
                raise Conflict("This request ID already recorded another link.")
            return read(db, term_id)
        current = plan(db, term_id)
        row = next((item for item in current["items"] if item["item"] == data.get("item")), None)
        if not row:
            raise ValueError("That planned row is not on this term's class schedule. Read it again.")
        target = {"assignment_id": assignment} if assignment else {"gradebook_item": column} if column else None
        if target == row["link"]:
            return read(db, term_id)
        if action == "link":
            if assignment:
                found = db.execute("SELECT definition_id FROM assignments WHERE id=?", (str(assignment),)).fetchone() if _has(db, "assignments") else None
                if not found or found[0] != term["definition_id"]:
                    raise ValueError("That assignment is not one of this course's.")
            elif not any(item["id"] == column and not item.get("archived") for item in _setup(db, term_id).get("items", [])):
                raise ValueError("That column is not in this term's gradebook.")
            other = next((item for item in current["items"] if item["link"] == target), None)
            if other:
                raise Conflict(f"{other['title']} already follows that record. Unlink it first.")
        db.execute("""INSERT INTO scheduling_plan_links(id, term_id, item_id, action, assignment_id, gradebook_item, request_id,
            request_sha256, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (uuid.uuid4().hex[:16], term_id, row["item"], action, assignment or None, column or None, request_id, digest, actor, timestamp()))
        db.commit()
        return read(db, term_id)


def _setup(db, term_id):
    from canvas.placement import _setup as latest
    return latest(db, term_id)


# -- One read's memo of a term ---------------------------------------------------------------------------------------------------------
class TermRead:
    """Everything a term's sections' schedules read, loaded once per read and shared by its sections: the Term schedule and whether
    the term uses it, the plan, the gradebook setup, Faculty's Canvas copy, the day each assessment is given to each section, the
    certified assessments and the notes outline. Catalog only; no lock, nothing outside Faculty."""

    def __init__(self, db, term_id):
        from canvas.placement import _table
        from certified_archive import certified
        from course_groups import navigation_id
        from course_notes.service import current_sections
        from exam_workflow.given_dates import term_given
        from seating import term_label
        term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (str(term_id),)).fetchone()
        if not term:
            raise NotFound("That course term was not found.")
        self.db, self.term_id, self.term = db, term["id"], term
        self.code, self.label = term["code"], term_label(term)
        self.group = navigation_id({"id": term["id"], "code": term["code"], "term": term["term"], "term_other": term["term_other"],
                                    "year": term["year"]})
        self.sections = {row[0]: row[1] for row in db.execute("SELECT id, number FROM course_sections WHERE term_id=?", (self.term_id,))}
        self.schedule, self.use = ScheduleService.revision(db, self.term_id), current_use(db, self.term_id)
        self.plan = plan(db, self.term_id)
        self.setup = _setup(db, self.term_id)
        self.content = self.known = self.copy_canvas = None
        if _table(db, "canvas_content_revisions"):
            from canvas.column_assignments import _copy_canvas, _known
            from canvas.content_service import CanvasContentService
            self.content = CanvasContentService._latest(db, self.term_id)[0]
            self.known = _known(db, self.term_id)
            self.copy_canvas = _copy_canvas(db, self.term_id) if _table(db, "canvas_content_links") else {}
        self.courses = {}
        if _table(db, "canvas_course_links"):
            from canvas.service import _link
            for section in self.sections:
                found = _link(db, section)
                self.courses[section] = str(found["course_id"]) if found else None
        self.given = term_given(db, self.term_id)
        self.certified = {key[0] for key in certified(db) if key[1] == self.term_id}
        self.notes = current_sections(db, term["definition_id"])
        self.works = self._works()
        self._meetings, self._hubs = {}, {}

    # -- The same work, joined ---------------------------------------------------------------------------------------------------
    def _works(self):
        """The term's work, each joined once: by its course-wide assignment (exam record, seating charts, the columns and the copy's
        assignment that name it), else by its gradebook column, else the copy's assignment alone, else a planned row alone."""
        from canvas.column_assignments import stands_for
        works, taken = {}, set()
        columns = [item for item in self.setup.get("items", []) if not item.get("archived")]
        key_of = {}

        def work(key, assignment=None):
            return works.setdefault(key, {"key": key, "assignment_id": assignment, "columns": [], "copy": None, "plan": None, "missing": False})

        for assignment in self.given:
            work(f"a:{assignment}", assignment)
        for item in columns:
            assignment = item.get("assignment_id") or None
            found = work(f"a:{assignment}", assignment) if assignment else work(f"g:{item['id']}")
            key_of[item["id"]] = found["key"]
            found["columns"].append(item)
            copy = stands_for(self.content, item, self.known, self.copy_canvas) if self.content else None
            if copy:
                taken.add(copy["id"])
                found["copy"] = found["copy"] or copy
        for entry in (self.content or {}).get("assignments", []):
            if entry["id"] in taken:
                continue
            if entry.get("faculty_assignment"):
                found = work(f"a:{entry['faculty_assignment']}", entry["faculty_assignment"])
                found["copy"] = found["copy"] or entry
            elif not entry.get("gradebook_item"):  # one standing for an archived column is out of view with it
                work(f"c:{entry['id']}")["copy"] = entry
        for row in self.plan["items"]:
            target = row.get("link") or {}
            if target.get("assignment_id"):
                found = work(f"a:{target['assignment_id']}", target["assignment_id"])
            elif target.get("gradebook_item") in key_of:
                found = works[key_of[target["gradebook_item"]]]
            else:
                found = work(f"p:{row['item']}")
                found["missing"] = bool(target)  # the column it followed left the gradebook: planned again, the page offers Unlink
            if found["plan"] is None:
                found["plan"] = row
        wanted = sorted({found["assignment_id"] for found in works.values() if found["assignment_id"]})
        titles = {}
        if wanted and _has(self.db, "assignments"):
            marks = ",".join("?" * len(wanted))
            titles = dict(self.db.execute(f"SELECT id, title FROM assignments WHERE id IN ({marks})", wanted).fetchall())
        for found in works.values():
            found["title"] = (titles.get(found["assignment_id"]) or next((item["title"] for item in found["columns"]), "")
                              or (found["copy"] or {}).get("name") or (found["plan"] or {}).get("title") or "")
            found["archived"] = found["assignment_id"] in self.certified
        return list(works.values())

    # -- One section's facts of one work -----------------------------------------------------------------------------------------
    def _row_for(self, work, section):
        row = work["plan"]
        return row if row and (not row["sections"] or section in row["sections"]) else None

    def _copy_due(self, work, section):
        """(campus date, "HH:MM", problem) of the copy's assignment for this section's Canvas course, ("", "", "") when it has none."""
        from canvas import content as model
        from canvas.placement import campus_parts
        copy = work["copy"]
        if not copy or not model.applies(copy, [section]):
            return "", "", ""
        course = self.courses.get(section)
        sharing = [other for other in self.sections if course and self.courses.get(other) == course and model.applies(copy, [other])] or [section]
        instant, problem = model.due_for(copy, sharing)
        return (*campus_parts(instant), problem)

    def _real_given(self, work, section):
        return (self.given.get(work["assignment_id"]) or {}).get(section) if work["assignment_id"] else None

    def facts(self, work, section):
        """What this section's schedule says of one work: {given, due, differs, unclear} (given and due each None or {date, time,
        place, source, planned, planned_was, href}), or None when the work is not this section's."""
        from gradebook.setup import applies
        real, row = self._real_given(work, section), self._row_for(work, section)
        columns = [item for item in work["columns"] if applies(item, section)]
        copy_date, copy_time, problem = self._copy_due(work, section)
        if not (real or row or columns or copy_date or problem):
            return None
        own = (row or {}).get("section_values", {}).get(section, {})
        role = "given" if (real or (row and row["kind"] in GIVEN)) else "date" if (row and row["kind"] == "other") else "due"
        given = due = None
        differs, dates = [], {}
        # The day, time and place it is given: the most specific record that has each (module docstring).
        if role != "due":
            values, origin = {}, {}
            for key, source in (("date", "source"), ("time", "time_source"), ("place", "place_source")):
                if real and real[key] and real[source] == "chart":
                    values[key], origin[key] = real[key], "seating"
                elif own.get(key):
                    values[key], origin[key] = own[key], "planned"
                elif real and real[key]:
                    values[key], origin[key] = real[key], "exam"
                else:
                    values[key], origin[key] = (row or {}).get(key, ""), "planned" if (row or {}).get(key) else ""
            if values["date"]:
                planned = (own.get("date") or (row or {}).get("date", "")) if row else ""
                given = {**values, "source": origin["date"], "planned": origin["date"] == "planned",
                         "planned_was": planned if planned and planned != values["date"] else "",
                         "href": self._given_href(work, real, origin["date"])}
                dates["given"] = values["date"]
            if own.get("date") and real and real["date"] and real["source"] == "sitting" and own["date"] != real["date"]:
                differs.append("plan_given")
                dates["plan"], dates["given_record"] = own["date"], real["date"]
        # The day it is due: this section's date in Faculty's Canvas copy, else its gradebook column's, else the planned one.
        column = next((item for item in columns if item.get("due_date")), None)
        planned_due = ""
        if row:
            planned_due = (own.get("date") or row["date"]) if row["kind"] == "homework" else (own.get("due_date") or row["due_date"])
        if copy_date:
            due = {"date": copy_date, "time": copy_time, "place": "", "source": "canvas", "href": f"#course/{self.group}/home/canvas"}
        elif column:
            due = {"date": column["due_date"], "time": "", "place": "", "source": "gradebook", "href": self._column_href(column)}
        elif planned_due:
            due = {"date": planned_due, "time": (own.get("time") or row["time"]) if row["kind"] == "homework" else "",
                   "place": "", "source": "planned", "href": None}
        if due:
            due.update(planned=due["source"] == "planned", planned_was=planned_due if planned_due and planned_due != due["date"] else "")
        if column:
            dates["gradebook"] = column["due_date"]
        if copy_date:
            dates["canvas"] = copy_date
        apart = bool(row and row["kind"] in GIVEN and (own.get("due_date") or row["due_date"]))  # due apart from its day, on purpose
        if not work["archived"]:
            if column and copy_date and column["due_date"] != copy_date and column.get("canvas") != "off":
                latest = max((self._copy_due(work, other)[0] for other in self.sections if applies(column, other)), default="")
                if column["due_date"] != latest:
                    differs.append("gradebook_canvas")
            real_day = real["date"] if real else ""
            if real_day and not apart:
                if column and column["due_date"] != real_day:
                    latest = max(((self._real_given(work, other) or {}).get("date", "") for other in self.sections if applies(column, other)), default="")
                    if column["due_date"] != latest:
                        differs.append("given_gradebook")
                if copy_date and copy_date != real_day:
                    course = self.courses.get(section)
                    sharing = [other for other in self.sections if course and self.courses.get(other) == course] or [section]
                    if copy_date != max(((self._real_given(work, other) or {}).get("date", "") for other in sharing), default=""):
                        differs.append("given_canvas")
        else:
            differs = []
        return {"given": given, "due": due, "role": role, "row": row, "differs": [rule for rule in RULES if rule in differs],
                "dates": dates, "unclear": problem, "column": column}

    def _given_href(self, work, real, origin):
        from workspace_calendar import exam_hub
        if origin == "seating" and real and real["source"] == "chart":
            return f"#seating/{real['source_id']}"
        if origin == "planned" or not work["assignment_id"]:
            return None
        return exam_hub(self.db, self.term_id, work["assignment_id"], self._hubs) if _has(self.db, "exam_events") else None

    def _column_href(self, column):
        return f"#gradebook/{self.term_id}?item={column['id']}"

    # -- Class days ----------------------------------------------------------------------------------------------------------------
    def meetings(self, section):
        """({date: [{time, end_time, room}]}, {days off}) of a section (`section_meetings`), kept for the read."""
        if section not in self._meetings:
            self._meetings[section] = section_meetings(self.db, self.term_id, section, self.schedule, self.use)
        return self._meetings[section]

    def topics(self, section, day):
        """A class day's topics for a section: its own entry when it has one, else the term's; None when neither says anything."""
        entry = self.plan["topics"].get(day)
        if not entry:
            return None
        own = entry.get("sections", {}).get(section)
        chosen = own or entry
        if not chosen["notes"] and not chosen["note"]:
            return None
        return {"notes": [{"number": number, "title": (self.notes.get(number) or {}).get("title", ""), "known": number in self.notes,
                           "url": (self.notes.get(number) or {}).get("url", "")} for number in chosen["notes"]],
                "note": chosen["note"], "own": bool(own)}


# -- Class days -------------------------------------------------------------------------------------------------------------------------
def section_meetings(db, term_id, section, schedule=None, use=None):
    """({date: [{time, end_time, room}]}, {days off}) of a section: what the Calendar shows as its class meetings (the Term
    schedule's while the term uses it, and the per-date class events the Use left showing: a day run on another day's schedule),
    and the days off its weekly meetings would otherwise fill. `schedule` and `use` are the term's, when the caller read them."""
    schedule = schedule if schedule is not None else ScheduleService.revision(db, term_id)
    use = use if use is not None else current_use(db, term_id)
    days, replaced, weekdays = {}, set(), set()
    if use and schedule["revision"]:
        replaced = {row[0] for row in db.execute("SELECT calendar_event_id FROM scheduling_replaced_events WHERE use_id=?", (use["id"],))}
        for row in occurrences(db, term_id, schedule["first_day"], schedule["last_day"], revision=schedule):
            if row["schedule_kind"] == "class" and row["section_id"] == section:
                days.setdefault(row["date"], []).append({"time": row["time"], "end_time": row["end_time"], "room": row["location"]})
        weekdays = {day for entry in schedule["entries"] if entry["kind"] == "class" and entry["section_id"] == section
                    for day in entry["weekdays"]}
    for row in _legacy(db, term_id):
        if row["kind"] == "class" and row["section_id"] == section and row["id"] not in replaced:
            days.setdefault(row["date"], []).append({"time": row["time"] or "", "end_time": row["end_time"] or "",
                                                     "room": " ".join(str(row["location"] or "").split())})
    for meetings in days.values():
        meetings.sort(key=lambda meeting: (meeting["time"], meeting["end_time"]))
    off = {day for day in schedule.get("days_off", []) if day not in days and dt.date.fromisoformat(day).weekday() in weekdays}
    return days, off


# -- A section's schedule ----------------------------------------------------------------------------------------------------------
def _clock_order(text):
    found = clock.parse_span(text) if text else None
    return found[0] if found else 24 * 60


def _differ_text(rule, dates, number):
    label = lambda key: clock.day_label(dt.date.fromisoformat(dates[key]))  # noqa: E731
    if rule == "gradebook_canvas":
        return f"The gradebook says due {label('gradebook')}; Faculty's Canvas copy says {label('canvas')}."
    if rule == "given_gradebook":
        return f"Given {label('given_record') if 'given_record' in dates else label('given')}; the gradebook says due {label('gradebook')}."
    if rule == "given_canvas":
        return f"Given {label('given_record') if 'given_record' in dates else label('given')}; Faculty's Canvas copy says due {label('canvas')}."
    return f"Section {number}'s own planned day is {label('plan')}; the exam record says {label('given_record')} for every section."


def _recorded(db, section, memo):
    """{date: what the recordings filed on that class day reached}, read from the Recordings module that owns it. A reading
    that cannot be set against the plan never keeps the schedule itself from being read."""
    import logging
    import sqlite3
    try:
        from recordings import classes
        return classes.recorded(db, section, memo)
    except (sqlite3.Error, AttributeError, KeyError, TypeError, ValueError, NotFound):
        logging.getLogger(__name__).warning("What a section's recorded classes reached could not be read", exc_info=True)
        return {}


def section_schedule(db, section_id, start=None, end=None, *, term=None, whole=False):
    """One section's class schedule over [start, end]: {…, days: [{date, weekday, meetings, off, topics, work, and `recorded` on
    a day a class recording is filed on}], undated, differs, unclear, open_planned}. Without dates: two weeks back to four ahead of the campus day, or with `whole` every date the schedule
    has. `term` is a TermRead of the section's term to share between sections of one read."""
    from workspace_calendar import date_range
    found = db.execute("SELECT id, term_id, number FROM course_sections WHERE id=?", (str(section_id),)).fetchone()
    if not found:
        raise NotFound("That section was not found.")
    section, number = found[0], found[2]
    memo = term if term is not None and term.term_id == found[1] else TermRead(db, found[1])
    today = clock.campus_today()
    meetings, off = memo.meetings(section)
    days, undated, differs, unclear, open_planned = {}, [], [], [], 0

    def day_of(date):
        return days.setdefault(date, {"date": date, "weekday": dt.date.fromisoformat(date).strftime("%a"), "meetings": [], "off": False,
                                      "topics": None, "work": []})

    for date, rows in meetings.items():
        day_of(date)["meetings"] = rows
    for date in off:
        day_of(date)["off"] = True
    for date in memo.plan["topics"]:
        topics = memo.topics(section, date)
        if topics:
            day_of(date)["topics"] = topics
    for work in memo.works:
        facts = memo.facts(work, section)
        if not facts:
            continue
        row = facts["row"]
        linked = bool(row and row.get("link") and not work["missing"])
        if row and not linked:
            open_planned += 1
        base = {"key": work["key"], "title": (row["title"] if row and not linked else work["title"]) or (row or {}).get("title", ""),
                "kind": row["kind"] if row else "", "item": row["item"] if row else None, "linked": linked,
                "note": row["note"] if row else "", "archived": work["archived"], "differs": facts["differs"]}
        given, due = facts["given"], facts["due"]
        if due and given and due["date"] == given["date"]:
            due = None  # due the day it is given: one line says it
        for what, fact in (("given" if facts["role"] == "given" else "date", given), ("due", due)):
            if fact:
                day_of(fact["date"])["work"].append({**base, "what": what, **fact})
        if not given and not due and row:
            own = row["section_values"].get(section, {})
            undated.append({**base, "what": facts["role"], "time": own.get("time") or row["time"], "place": own.get("place") or row["place"]})
        for rule in facts["differs"]:
            hrefs = {"given": (given or {}).get("href"), "gradebook": memo._column_href(facts["column"]) if facts["column"] else None,
                     "canvas": f"#course/{memo.group}/home/canvas" if "canvas" in facts["dates"] else None}
            differs.append({"key": work["key"], "title": base["title"], "rule": rule, "dates": facts["dates"],
                            "date": (given or due or {}).get("date", ""), "text": _differ_text(rule, facts["dates"], number),
                            "hrefs": {key: value for key, value in hrefs.items() if value}})
        if facts["unclear"]:
            unclear.append({"key": work["key"], "title": base["title"], "problem": facts["unclear"]})
    # WHAT A RECORDING REACHED (RECORDINGS "Classes (step 5)"): a class day a recording is filed on says, by reference, which
    # notes sections its reading kept as worked through and which planned ones were not reached. Only such a day carries the
    # key, so a schedule with no class recording reads exactly as before.
    for date, entry in _recorded(db, section, memo).items():
        if date in days:
            days[date]["recorded"] = entry
    for day in days.values():
        day["work"].sort(key=lambda row: (("given", "date", "due").index(row["what"]), _clock_order(row["time"]), row["title"].casefold()))
    if start is None and end is None:
        if whole:
            known = sorted(days) or [today.isoformat()]
            start, end = known[0], known[-1]
        else:
            start, end = (today - dt.timedelta(days=BEFORE)).isoformat(), (today + dt.timedelta(days=AFTER)).isoformat()
    else:
        start, end = date_range(start, end)
    return {"section_id": section, "section": number, "term_id": memo.term_id, "group_id": memo.group, "code": memo.code,
            "term_label": memo.label, "start": start, "end": end, "today": today.isoformat(), "revision": memo.plan["revision"],
            "term_schedule": {"revision": memo.schedule["revision"], "in_use": bool(memo.use), "first_day": memo.schedule["first_day"],
                              "last_day": memo.schedule["last_day"]},
            "days": [days[date] for date in sorted(days) if start <= date <= end],
            "undated": sorted(undated, key=lambda row: row["title"].casefold()),
            "differs": sorted((entry for entry in differs if not entry["date"] or start <= entry["date"] <= end),
                              key=lambda entry: (entry["date"], entry["title"].casefold(), RULES.index(entry["rule"]))),
            "unclear": unclear, "open_planned": open_planned}


# -- Pre-fill: the planned rows no record follows yet ------------------------------------------------------------------------------
def open_items(db, term_id, kind=None, memo=None):
    """The term's planned rows that follow no record yet, for a form that is about to create one (a quiz approval, a new column):
    each as stored, with `match`, the term's unlinked assignment or gradebook column of the same title when exactly one has it."""
    memo = memo or TermRead(db, term_id)
    followed = {json.dumps(row["link"], sort_keys=True) for row in memo.plan["items"] if row.get("link")}
    names = {}
    for work in memo.works:
        if work["key"].startswith("a:") and json.dumps({"assignment_id": work["assignment_id"]}, sort_keys=True) not in followed:
            names.setdefault(work["title"].casefold(), []).append({"assignment_id": work["assignment_id"], "title": work["title"]})
        for item in work["columns"] if not work["key"].startswith("a:") else ():
            if json.dumps({"gradebook_item": item["id"]}, sort_keys=True) not in followed:
                names.setdefault(item["title"].casefold(), []).append({"gradebook_item": item["id"], "title": item["title"]})
    rows = []
    for work in memo.works:
        row = work["plan"]
        if not row or (row.get("link") and not work["missing"]) or (kind and row["kind"] != kind):
            continue
        same = names.get(row["title"].casefold(), [])
        rows.append({**{key: value for key, value in row.items() if key != "link"}, "missing": work["missing"],
                     "match": same[0] if len(same) == 1 else None})
    return sorted(rows, key=_order)
