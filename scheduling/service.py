"""SCHEDULING "TERM SCHEDULE" (stage 1c, 2026-10-05): a course term's dates and weekly entries, the source of truth for its class meetings,
office hours and meeting hours once the term uses it (stage 1d). Term-owned immutable revisions (`scheduling_revisions`), each weekly
entry one stable identity (`scheduling_entries`; a class meeting also names its section). Edited on the course's Term schedule page;
an assistant write is an Approve card.

An entry: {entry, kind, section_id (a class), weekdays [0 Monday..6], start, end "HH:MM", room, seats_makeup}. `seats_makeup` says
an office-hours room is big enough to seat a student taking a makeup apart from visitors (ABSENCES "MAKEUP BOOKING" ROOMS); meeting
hours always host makeups. `import_proposal` reads what the term's calendar already says (the per-date "· Section N" and "· Office
hours" events, the meeting hours) so the first revision need not be typed; it writes nothing."""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import digest as request_digest, encoded
from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect

from .clock import parse_clock

KINDS = ("class", "office_hours", "meeting_hours")
MAX_ENTRIES, MAX_DAYS, ROOM_LIMIT, MIN_MINUTES, MAX_DAYS_OFF = 60, 200, 80, 10, 60
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _day(value):
    try:
        return dt.date.fromisoformat(str(value)).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)) else None
    except ValueError:
        return None


class ScheduleService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "scheduling", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ---------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _term(db, term_id):
        term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone()
        if not term:
            raise NotFound("That course term was not found.")
        return term

    @staticmethod
    def _sections(db, term_id):
        return [{"id": row[0], "number": row[1]} for row in db.execute(
            "SELECT id, number FROM course_sections WHERE term_id=? ORDER BY CAST(number AS INTEGER), number", (term_id,))]

    @staticmethod
    def revision(db, term_id):
        """The term's latest revision {revision, first_day, last_day, entries, actor, updated_at}; revision 0 when none was saved."""
        row = db.execute("""SELECT revision, first_day, last_day, entries_json, actor, created_at, days_off_json FROM scheduling_revisions WHERE term_id=?
            ORDER BY revision DESC LIMIT 1""", (term_id,)).fetchone()
        if not row:
            return {"revision": 0, "first_day": "", "last_day": "", "entries": [], "days_off": [], "actor": "", "updated_at": ""}
        kinds = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, kind, section_id FROM scheduling_entries WHERE term_id=?", (term_id,))}
        entries = [{**item, "kind": kinds[item["entry"]][0], "section_id": kinds[item["entry"]][1]} for item in json.loads(row[3])]
        return {"revision": row[0], "first_day": row[1], "last_day": row[2], "entries": entries, "days_off": json.loads(row[6]),
                "actor": row[4], "updated_at": row[5]}

    def group_term(self, group_id):
        """The term a course page's group ID names (a navigation projection of one course code and term)."""
        from course_groups import navigation_id
        with connect(self.path) as db:
            for term in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL"):
                if navigation_id({"id": term["id"], "code": term["code"], "term": term["term"], "term_other": term["term_other"],
                                  "year": term["year"]}) == group_id:
                    return term["id"]
        raise NotFound("That course and term were not found.")

    def read(self, term_id):
        with connect(self.path) as db:
            return self.read_in(db, term_id)

    # -- Saving ----------------------------------------------------------------------------------------------------------------
    def _normalize(self, db, term_id, data):
        first, last = _day(data.get("first_day")), _day(data.get("last_day"))
        if not first or not last:
            raise ValueError("Give the term's first and last day of classes.")
        if last < first or (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days > MAX_DAYS:
            raise ValueError(f"The last day must come after the first, within {MAX_DAYS} days.")
        items = data.get("entries")
        if not isinstance(items, list) or len(items) > MAX_ENTRIES:
            raise ValueError(f"Send the weekly schedule as a list of at most {MAX_ENTRIES} entries.")
        known = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, kind, section_id FROM scheduling_entries WHERE term_id=?", (term_id,))}
        sections = {s["id"]: s["number"] for s in self._sections(db, term_id)}
        entries, seen = [], set()
        for item in items:
            if not isinstance(item, dict) or set(item) - {"entry", "kind", "section_id", "weekdays", "start", "end", "room", "seats_makeup"}:
                raise ValueError("Give each weekly entry its kind, days, start, end and room.")
            ident = item.get("entry")
            if ident is not None:
                if not isinstance(ident, str) or ident not in known or ident in seen:
                    raise ValueError("A weekly entry is not one of this term's (or is listed twice). Read the schedule again.")
                kind, section = known[ident]
                if (item.get("kind", kind), item.get("section_id", section)) != (kind, section):
                    raise ValueError("An entry keeps its kind and section; add a new entry instead.")
                seen.add(ident)
            else:
                kind, section = item.get("kind"), item.get("section_id")
                if kind not in KINDS:
                    raise ValueError("Each entry is a class meeting, office hours or meeting hours.")
                if (kind == "class") != bool(section) or (section and (not isinstance(section, str) or section not in sections)):
                    raise ValueError("A class meeting names one of this term's sections; office and meeting hours name none.")
            days = item.get("weekdays")
            if (not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days)
                    or len(set(days)) != len(days)):
                raise ValueError("Choose the days of the week for each entry.")
            start, end = parse_clock(item.get("start")), parse_clock(item.get("end"))
            if start is None or end is None or end - start < MIN_MINUTES:
                raise ValueError(f"Give each entry a start and an end at least {MIN_MINUTES} minutes later, such as 13:00 and 17:00.")
            room = " ".join(str(item.get("room") or "").split())
            if len(room) > ROOM_LIMIT:
                raise ValueError(f"Keep each room to {ROOM_LIMIT} characters.")
            seats = item.get("seats_makeup", kind == "meeting_hours")
            if not isinstance(seats, bool):
                raise ValueError("Say whether the room can seat a makeup.")
            entries.append({"entry": ident, "kind": kind, "section_id": section if kind == "class" else None, "weekdays": sorted(days),
                            "start": item["start"], "end": item["end"], "room": room,
                            "seats_makeup": True if kind == "meeting_hours" else (seats if kind == "office_hours" else False)})
        meeting = [(d, parse_clock(e["start"]), parse_clock(e["end"])) for e in entries if e["kind"] == "meeting_hours" for d in e["weekdays"]]
        for a, b in ((a, b) for i, a in enumerate(meeting) for b in meeting[i + 1:]):
            if a[0] == b[0] and a[1] < b[2] and b[1] < a[2]:
                raise ValueError(f"Two meeting-hours times on {DAY_NAMES[a[0]]} overlap; join them into one.")
        order = {"class": 0, "office_hours": 1, "meeting_hours": 2}
        entries.sort(key=lambda e: (order[e["kind"]], sections.get(e["section_id"] or "", ""), e["weekdays"], e["start"]))
        days_off = data.get("days_off", [])
        if not isinstance(days_off, list) or len(days_off) > MAX_DAYS_OFF or any(_day(d) != d or not first <= d <= last for d in days_off):
            raise ValueError(f"Days off are at most {MAX_DAYS_OFF} dates between the first and last day of classes.")
        return first, last, entries, sorted(set(days_off))

    @staticmethod
    def _stored(entries):
        return [{k: e[k] for k in ("entry", "weekdays", "start", "end", "room", "seats_makeup")} for e in entries]

    def save(self, term_id, data, actor="workspace"):
        """Replace the term's schedule against the revision the page showed (`base_revision`, 0 for none). New entries (no `entry`)
        get their identity here; identical schedules record nothing. Exact request IDs."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = valid_request_id(data.get("request_id"))
        if type(data.get("base_revision")) is not int:
            raise ValueError("Name the revision of the schedule you edited as base_revision (0 for none).")
        digest = request_digest({"term": term_id, **{k: data.get(k) for k in ("first_day", "last_day", "entries")},
                                 **({"days_off": data["days_off"]} if "days_off" in data else {})})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._term(db, term_id)
            earlier = db.execute("SELECT request_sha256 FROM scheduling_revisions WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
            if earlier:
                if earlier[0] != digest:
                    raise Conflict("This request ID already saved a different schedule.")
                return self.read_in(db, term_id)
            current = self.revision(db, term_id)
            if data["base_revision"] != current["revision"]:
                raise Conflict("This schedule changed elsewhere. Read it again before saving.")
            first, last, entries, days_off = self._normalize(db, term_id, data)
            now = timestamp()
            for entry in entries:
                if entry["entry"] is None:
                    entry["entry"] = uuid.uuid4().hex[:16]
                    db.execute("INSERT INTO scheduling_entries(id, term_id, section_id, kind, created_at) VALUES (?,?,?,?,?)",
                               (entry["entry"], term_id, entry["section_id"], entry["kind"], now))
            stored = self._stored(entries)
            if (first, last, stored, days_off) != (current["first_day"], current["last_day"], self._stored(current["entries"]), current["days_off"]):
                db.execute("""INSERT INTO scheduling_revisions(term_id, revision, first_day, last_day, entries_json, days_off_json, request_id,
                    request_sha256, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                           (term_id, current["revision"] + 1, first, last, encoded(stored), encoded(days_off), request_id, digest, actor, now))
            db.commit()
            return self.read_in(db, term_id)

    def read_in(self, db, term_id):
        term = self._term(db, term_id)
        use = current_use(db, term_id)
        return {"term_id": term_id, "code": term["code"], "sections": self._sections(db, term_id), **self.revision(db, term_id),
                "in_use": bool(use), "in_use_since": use["created_at"] if use else ""}

    # -- A first revision from the calendar ------------------------------------------------------------------------------------
    def import_proposal(self, term_id):
        """What the term's calendar already says, as a schedule to review and save (writes nothing): the per-date class events of each
        section ("<code> · Section N") and office-hours events grouped into weekly entries (a day of the week, time and room seen on
        two or more dates), the term's dates from the first and last of them, and the meeting hours with their makeup rooms."""
        from student_support import meeting_hours
        from .engine import room_allows
        with connect(self.path) as db:
            term = self._term(db, term_id)
            sections = {s["id"]: s["number"] for s in self._sections(db, term_id)}
            rows = db.execute("""SELECT title, date, time, end_time, location, section_id FROM calendar_events
                WHERE term_id=? AND deleted_at IS NULL AND all_day=0 AND category='event' ORDER BY date, time""", (term_id,)).fetchall() \
                if _has(db, "calendar_events") else []
            hours = meeting_hours.read(db, term_id)
        groups, dates = {}, []
        for row in rows:
            title = str(row["title"] or "")
            section = row["section_id"] if row["section_id"] in sections and re.search(r"·\s*Section\s+\d+\s*$", title) else None
            kind = "class" if section else "office_hours" if "office hour" in title.casefold() and "make" not in title.casefold() else None
            start, end = parse_clock(row["time"]), parse_clock(row["end_time"])
            if not kind or start is None or end is None or end <= start:
                continue
            room = " ".join(str(row["location"] or "").split())
            weekday = dt.date.fromisoformat(row["date"]).weekday()
            groups.setdefault((kind, section, row["time"], row["end_time"], room), {}).setdefault(weekday, []).append(row["date"])
        entries = []
        for (kind, section, start, end, room), days in groups.items():
            weekly = sorted(d for d, seen in days.items() if len(seen) >= 2)
            if not weekly:
                continue
            dates += [day for d in weekly for day in days[d]]
            entries.append({"kind": kind, "section_id": section, "weekdays": weekly, "start": start, "end": end, "room": room,
                            "seats_makeup": kind == "office_hours" and room_allows(hours["makeup_rooms"], room)})
        # Entries of one kind, section, time and room on several days are one entry; so are meeting-hours windows.
        windows = [{"kind": "meeting_hours", "section_id": None, "weekdays": [w["weekday"]], "start": w["start"], "end": w["end"],
                    "room": w["location"], "seats_makeup": True} for w in hours["windows"]]
        merged = {}
        for entry in entries + windows:
            key = (entry["kind"], entry["section_id"], entry["start"], entry["end"], entry["room"], entry["seats_makeup"])
            if key in merged:
                merged[key]["weekdays"] = sorted(set(merged[key]["weekdays"]) | set(entry["weekdays"]))
            else:
                merged[key] = dict(entry)
        order = {"class": 0, "office_hours": 1, "meeting_hours": 2}
        proposal = sorted(merged.values(), key=lambda e: (order[e["kind"]], sections.get(e["section_id"] or "", ""), e["weekdays"], e["start"]))
        first, last = (min(dates), max(dates)) if dates else ("", "")
        days_off = self.days_off(term_id, first, last, proposal) if dates else []
        return {"term_id": term_id, "code": term["code"], "first_day": first, "last_day": last, "entries": proposal, "days_off": days_off,
                "from_events": len(rows)}

    def days_off(self, term_id, first, last, entries):
        """The dates in [first, last] the term's calendar keeps free of these weekly entries: every class or office-hours time the
        entries would put on that date has no matching per-date event (Labor Day, Thanksgiving, a day run on another day's schedule)."""
        trial = {"revision": 1, "first_day": first, "last_day": last, "days_off": [],
                 "entries": [{**e, "entry": e.get("entry") or f"trial{n}"} for n, e in enumerate(entries)]}
        with connect(self.path) as db:
            generated = occurrences(db, term_id, first, last, revision=trial)
            legacy = {_key(r["kind"], r["section_id"], r["date"], r["time"], r["end_time"], r["location"]) for r in _legacy(db, term_id)}
        by_date = {}
        for row in generated:
            key = _key(row["schedule_kind"], row["section_id"], row["date"], row["time"], row["end_time"], row["location"])
            by_date.setdefault(row["date"], []).append(key in legacy)
        return sorted(day for day, matched in by_date.items() if not any(matched))


def protect_references(db, previous, replacement=None):
    """A course edit must not detach a Term schedule: its term's code, term and year, and every section a class meeting names, stay
    covered by an offering (the calendar's rule, calendar_service.protect_references). The same holds for a term's class schedule
    (CLASS SCHEDULE) and the sections its planned rows and topics name. The caller holds the lifecycle lock."""
    if not _has(db, "scheduling_revisions"):
        return
    from rosters import decode_course, term_key
    plans = _has(db, "scheduling_plan_revisions")
    terms = db.execute(f"""SELECT t.id, t.definition_id, t.term, t.term_other, t.year FROM course_terms t
        WHERE t.definition_id=? AND t.deleted_at IS NULL AND (EXISTS (SELECT 1 FROM scheduling_revisions r WHERE r.term_id=t.id)
        {"OR EXISTS (SELECT 1 FROM scheduling_plan_revisions p WHERE p.term_id=t.id)" if plans else ""})""",
                       (previous["definition_id"],)).fetchall()
    if not terms:
        return
    offerings = [decode_course(row) for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL AND id!=? AND definition_id=?",
                                                          (previous["id"], previous["definition_id"]))]
    if replacement and replacement["definition_id"] == previous["definition_id"]:
        offerings.append(replacement)
    for term in terms:
        covered = [o for o in offerings if term_key(o) == term_key(term)]
        kept = {n for o in covered for n in o["sections"]}
        numbers = {row[0] for row in db.execute("""SELECT DISTINCT s.number FROM scheduling_entries e JOIN course_sections s ON s.id=e.section_id
            WHERE e.term_id=? AND e.kind='class'""", (term["id"],))}
        if db.execute("SELECT 1 FROM scheduling_revisions WHERE term_id=? LIMIT 1", (term["id"],)).fetchone() and (not covered or numbers - kept):
            raise Conflict("This change would detach a Term schedule. Keep its course, term and sections, or mark the course not "
                           "current to archive it.")
        if plans and db.execute("SELECT 1 FROM scheduling_plan_revisions WHERE term_id=? LIMIT 1", (term["id"],)).fetchone():
            from .class_schedule import named_sections
            named = named_sections(db, term["id"])
            planned = {row[1] for row in db.execute("SELECT id, number FROM course_sections WHERE term_id=?", (term["id"],)) if row[0] in named}
            if not covered or planned - kept:
                raise Conflict("This change would detach a class schedule. Keep its course, term and sections, or mark the course not "
                               "current to archive it.")


# -- USE THIS SCHEDULE (stage 1d) ---------------------------------------------------------------------------------------------------
PROJECTED = ("class", "office_hours")  # meeting hours are availability, never calendar blocks (requirement, 2026-10-05)
TITLES = {"office_hours": "Office hours"}


def _has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def current_use(db, term_id):
    """The term's latest Use receipt while it is in use (its latest receipt is a Use), else None."""
    if not _has(db, "scheduling_uses"):
        return None
    row = db.execute("SELECT * FROM scheduling_uses WHERE term_id=? ORDER BY rowid DESC LIMIT 1", (term_id,)).fetchone()
    return row if row and row["action"] == "use" else None


def _terms_in_use(db):
    if not _has(db, "scheduling_uses"):
        return []
    return [row[0] for row in db.execute("""SELECT u.term_id FROM scheduling_uses u WHERE u.rowid = (SELECT MAX(x.rowid)
        FROM scheduling_uses x WHERE x.term_id = u.term_id) AND u.action = 'use'""")]


def occurrences(db, term_id, start, end, *, revision=None):
    """The term's class meetings and office hours as calendar rows on each date of [start, end] inside its term dates."""
    from course_groups import navigation_id
    from seating import term_label
    term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone()
    found = revision or ScheduleService.revision(db, term_id)
    if not term or not found["revision"]:
        return []
    first, last = max(start, found["first_day"]), min(end, found["last_day"])
    if first > last:
        return []
    numbers = {row[0]: row[1] for row in db.execute("SELECT id, number FROM course_sections WHERE term_id=?", (term_id,))}
    group = navigation_id({"id": term["id"], "code": term["code"], "term": term["term"], "term_other": term["term_other"], "year": term["year"]})
    rows, day, stop, off = [], dt.date.fromisoformat(first), dt.date.fromisoformat(last), set(found.get("days_off") or ())
    while day <= stop:
        for entry in found["entries"] if day.isoformat() not in off else ():
            if entry["kind"] not in PROJECTED or day.weekday() not in entry["weekdays"]:
                continue
            section = numbers.get(entry["section_id"]) if entry["kind"] == "class" else None
            rows.append({"id": f"schedule:{entry['entry']}:{day.isoformat()}", "source": "schedule", "schedule_kind": entry["kind"],
                         "schedule_entry": entry["entry"], "title": f"Section {section}" if section is not None else TITLES[entry["kind"]],
                         "date": day.isoformat(), "end_date": day.isoformat(), "time": entry["start"], "end_time": entry["end"], "all_day": False,
                         "location": entry["room"], "notes": "", "definition_id": term["definition_id"], "term_id": term_id,
                         "section_id": entry["section_id"], "code": term["code"], "term_label": term_label(term),
                         "sections": [section] if section is not None else [], "href": f"#course/{group}/home/schedule", "editable": False,
                         "version": found["revision"], "time_basis": "campus", "category": "event", "seats_makeup": entry["seats_makeup"]})
        day += dt.timedelta(days=1)
    return rows


def projection(db, start, end):
    """(hidden, rows) for the calendar feed: the custom-event ids that terms in use replaced, and their schedules' rows."""
    hidden, rows = set(), []
    for term_id in _terms_in_use(db):
        use = current_use(db, term_id)
        hidden |= {f"custom:{row[0]}" for row in db.execute("SELECT calendar_event_id FROM scheduling_replaced_events WHERE use_id=?", (use["id"],))}
        rows += occurrences(db, term_id, start, end)
    return hidden, rows


def availability(db, term_id):
    """{windows, makeup_rooms, source}: the meeting hours ([{weekday, start, end, location}]) and the office-hours rooms that seat a
    makeup, from the Term schedule while the term uses it, else from the legacy meeting hours (student_support/meeting_hours.py)."""
    from student_support import meeting_hours
    if term_id and current_use(db, term_id):
        found = ScheduleService.revision(db, term_id)
        windows = sorted(({"weekday": day, "start": e["start"], "end": e["end"], "location": e["room"]} for e in found["entries"]
                          if e["kind"] == "meeting_hours" for day in e["weekdays"]), key=lambda w: (w["weekday"], w["start"]))
        rooms = list(dict.fromkeys(e["room"] for e in found["entries"] if e["kind"] == "office_hours" and e["seats_makeup"] and e["room"]))
        return {"windows": windows, "makeup_rooms": rooms, "source": "schedule"}
    legacy = meeting_hours.read(db, term_id)
    return {"windows": legacy["windows"], "makeup_rooms": legacy["makeup_rooms"], "source": "legacy"}


def _legacy(db, term_id):
    """The term's per-date custom events a schedule may replace: "<code> · Section N" class events and office-hours events."""
    if not _has(db, "calendar_events"):
        return []
    found = []
    for row in db.execute("""SELECT id, version, title, date, time, end_time, location, section_id FROM calendar_events
            WHERE term_id=? AND deleted_at IS NULL AND all_day=0 AND category='event' ORDER BY date, time, id""", (term_id,)):
        title = str(row["title"] or "")
        kind = "class" if row["section_id"] and re.search(r"·\s*Section\s+\d+\s*$", title) else \
            "office_hours" if "office hour" in title.casefold() and "make" not in title.casefold() else None
        if kind:
            found.append({**dict(row), "kind": kind})
    return found


def _key(kind, section, day, start, end, room):
    return (kind, section if kind == "class" else None, day, start, end, " ".join(str(room or "").split()).casefold())


class Plan:
    """What Use would do now (writes nothing): the generated occurrences matched one for one with legacy events, the legacy events
    left showing, and the dates the schedule adds."""

    def __init__(self, db, term_id):
        found = ScheduleService.revision(db, term_id)
        if not found["revision"]:
            raise Conflict("Save the Term schedule before using it.")
        self.revision = found["revision"]
        generated = occurrences(db, term_id, found["first_day"], found["last_day"], revision=found)
        waiting = {}
        for row in _legacy(db, term_id):
            waiting.setdefault(_key(row["kind"], row["section_id"], row["date"], row["time"], row["end_time"], row["location"]), []).append(row)
        self.matched, self.added = [], []
        for row in generated:
            pool = waiting.get(_key(row["schedule_kind"], row["section_id"], row["date"], row["time"], row["end_time"], row["location"]))
            if pool:
                self.matched.append((row, pool.pop(0)))
            else:
                self.added.append(row)
        self.left = sorted((row for pool in waiting.values() for row in pool), key=lambda r: (r["date"], r["time"]))
        self.fingerprint = request_digest({
            "revision": self.revision, "matched": sorted((m[1]["id"], m[1]["version"], m[0]["id"]) for m in self.matched),
            "left": [(r["id"], r["version"]) for r in self.left], "added": [r["id"] for r in self.added]})

    def view(self, today):
        show = lambda r, title: {"date": r["date"], "time": r["time"], "end_time": r["end_time"], "title": title, "location": r.get("location") or ""}  # noqa: E731
        counts = {}
        for row, _ in self.matched:
            counts[row["schedule_kind"]] = counts.get(row["schedule_kind"], 0) + 1
        return {"revision": self.revision, "fingerprint": self.fingerprint, "matched": len(self.matched), "matched_by_kind": counts,
                "left": [show(r, r["title"]) for r in self.left[:60]], "left_count": len(self.left),
                "added": [show(r, f"{r['code']} · {r['title']}") for r in self.added[:60]], "added_count": len(self.added),
                "replaced_from_today": sum(1 for _, legacy in self.matched if legacy["date"] >= today)}


def _use_request(data, confirmation):
    if not isinstance(data, dict):
        raise ValueError("Supply a JSON object.")
    request_id = valid_request_id(data.get("request_id"))
    if data.get("confirmation") != confirmation:
        raise ValueError(f"Confirm with {confirmation}.")
    return request_id, request_digest(data | {"request_id": None})


def _today():
    from exam_workflow.overview import campus_today
    return campus_today()


def use_preview(service, term_id):
    with connect(service.path) as db:
        service._term(db, term_id)
        return {"term_id": term_id, "in_use": bool(current_use(db, term_id)), **Plan(db, term_id).view(_today())}


def use(service, term_id, data, actor="workspace"):
    """Use the Term schedule: against the dry run the instructor saw (`fingerprint`), record the receipt and hide the matched events."""
    request_id, digest = _use_request(data, "use")
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        service._term(db, term_id)
        earlier = db.execute("SELECT * FROM scheduling_uses WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier:
            if earlier["request_sha256"] != digest:
                raise Conflict("This request ID already did something else.")
            return {"term_id": term_id, "in_use": bool(current_use(db, term_id)), **json.loads(earlier["result_json"])}
        if current_use(db, term_id):
            raise Conflict("This term already uses its Term schedule.")
        plan = Plan(db, term_id)
        if data.get("fingerprint") != plan.fingerprint:
            raise Conflict("The schedule or your calendar changed since the comparison. Look at it again before using it.")
        ident, now = uuid.uuid4().hex[:16], timestamp()
        result = {"action": "use", "revision": plan.revision, "hidden": len(plan.matched), "added": len(plan.added), "left": len(plan.left)}
        db.execute("""INSERT INTO scheduling_uses(id, term_id, action, revision, fingerprint, request_id, request_sha256, result_json, actor,
            created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""", (ident, term_id, "use", plan.revision, plan.fingerprint, request_id, digest, encoded(result), actor, now))
        db.executemany("INSERT INTO scheduling_replaced_events VALUES (?,?,?,?,?)",
                       [(ident, legacy["id"], legacy["version"], row["schedule_entry"], row["date"]) for row, legacy in plan.matched])
        db.commit()
        return {"term_id": term_id, "in_use": True, **result}


def undo(service, term_id, data, actor="workspace"):
    """Stop using the Term schedule: the hidden events show again and the schedule leaves the Calendar and Google."""
    request_id, digest = _use_request(data, "undo")
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        service._term(db, term_id)
        earlier = db.execute("SELECT * FROM scheduling_uses WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier:
            if earlier["request_sha256"] != digest:
                raise Conflict("This request ID already did something else.")
            return {"term_id": term_id, "in_use": bool(current_use(db, term_id)), **json.loads(earlier["result_json"])}
        use_row = current_use(db, term_id)
        if not use_row:
            raise Conflict("This term does not use its Term schedule.")
        restored = db.execute("SELECT COUNT(*) FROM scheduling_replaced_events WHERE use_id=?", (use_row["id"],)).fetchone()[0]
        result = {"action": "undo", "revision": use_row["revision"], "restored": restored}
        db.execute("""INSERT INTO scheduling_uses(id, term_id, action, revision, fingerprint, request_id, request_sha256, result_json, actor,
            created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex[:16], term_id, "undo", use_row["revision"], use_row["fingerprint"],
                                                        request_id, digest, encoded(result), actor, timestamp()))
        db.commit()
        return {"term_id": term_id, "in_use": False, **result}
