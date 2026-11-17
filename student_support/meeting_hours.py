"""TEACHING-WORKFLOWS MEETING HOURS (requirement, 2026-10-05).

A course term's weekly windows for individual meetings: [{weekday 0 (Monday)–6, start "HH:MM", end "HH:MM", location}], at most
MAX_WINDOWS, none overlapping on a day. Term-owned (course_terms.id, `student_support_meeting_hours`), so a new term starts with none.
Read by follow-up preparations (the open times a reply offers, and the bound on an office-hours appointment made by itself, ASSISTANT)
and by quiz makeup booking (its windows, and the office-hours rooms that can seat a makeup, `makeup_rooms`, ABSENCES "MAKEUP
BOOKING"). Plain values only: nothing here reads the calendar or imports another module's service."""
from __future__ import annotations

import json

from scheduling.clock import HHMM, parse_clock, twelve

MAX_WINDOWS = 14
MAX_ROOMS = 10
LOCATION_LIMIT = 80
DAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
CLOCK = HHMM


def minutes(value):
    return parse_clock(value, "hhmm")


def normalize(value):
    """Validate a complete replacement: the windows in weekday and time order, each location tidied. Raises ValueError saying what
    is wrong."""
    if not isinstance(value, list):
        raise ValueError("Send the meeting hours as a list of weekly times.")
    if len(value) > MAX_WINDOWS:
        raise ValueError(f"Keep meeting hours to {MAX_WINDOWS} weekly times at most.")
    windows = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {"weekday", "start", "end", "location"}:
            raise ValueError("Give each meeting time a weekday, a start, an end and a room.")
        weekday, start, end = entry.get("weekday"), entry.get("start"), entry.get("end")
        location = " ".join(str(entry.get("location") or "").split())
        if type(weekday) is not int or not 0 <= weekday <= 6:
            raise ValueError("Choose a weekday for each meeting time.")
        if not isinstance(start, str) or not isinstance(end, str) or not CLOCK.fullmatch(start) or not CLOCK.fullmatch(end):
            raise ValueError("Give each meeting time a start and an end, such as 13:00 and 17:00.")
        if minutes(end) - minutes(start) < 30:
            raise ValueError(f"A meeting time on {DAYS[weekday]} must end at least 30 minutes after it starts.")
        if len(location) > LOCATION_LIMIT:
            raise ValueError(f"Keep each room to {LOCATION_LIMIT} characters.")
        windows.append({"weekday": weekday, "start": start, "end": end, "location": location})
    windows.sort(key=lambda w: (w["weekday"], w["start"]))
    for before, after in zip(windows, windows[1:]):
        if before["weekday"] == after["weekday"] and minutes(after["start"]) < minutes(before["end"]):
            raise ValueError(f"Two meeting times on {DAYS[before['weekday']]} overlap; join them into one.")
    return windows


def normalize_rooms(value):
    """MAKEUP ROOMS (requirement, 2026-10-05): the office-hours rooms where a quiz makeup can be taken during office hours, tidied, each once."""
    if not isinstance(value, list) or len(value) > MAX_ROOMS:
        raise ValueError(f"Send the makeup rooms as a list of at most {MAX_ROOMS} rooms.")
    rooms, seen = [], set()
    for entry in value:
        room = " ".join(str(entry if isinstance(entry, str) else "").split())
        if not room or len(room) > LOCATION_LIMIT:
            raise ValueError(f"Name each makeup room in 1 to {LOCATION_LIMIT} characters.")
        if room.casefold() not in seen:
            seen.add(room.casefold())
            rooms.append(room)
    return rooms


def read(db, term_id):
    """The term's latest meeting hours as {revision, windows, actor, updated_at}; revision 0 and no windows when none were saved (or
    the catalog predates them)."""
    none = {"revision": 0, "windows": [], "makeup_rooms": [], "actor": "", "updated_at": ""}
    if not term_id or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='student_support_meeting_hours'").fetchone():
        return none
    row = db.execute("""SELECT revision,windows_json,actor,created_at,makeup_rooms_json FROM student_support_meeting_hours WHERE term_id=?
        ORDER BY revision DESC LIMIT 1""", (term_id,)).fetchone()
    if not row:
        return none
    return {"revision": row[0], "windows": json.loads(row[1]), "makeup_rooms": json.loads(row[4]), "actor": row[2], "updated_at": row[3]}


def on(windows, weekday):
    """[(start minute, end minute, location)] of one weekday, in time order."""
    return [(minutes(w["start"]), minutes(w["end"]), w["location"]) for w in windows if w["weekday"] == weekday]


def contains(windows, weekday, start, end):
    """Whether [start, end) minutes of that weekday lie inside one window."""
    return any(low <= start and end <= high for low, high, _ in on(windows, weekday))


def describe(windows):
    """"Tuesdays and Thursdays · 1:00 pm–5:00 pm · BLOC 319": days that share a time and room said together, in weekday order."""
    groups = {}
    for w in windows:
        groups.setdefault((w["start"], w["end"], w["location"]), []).append(w["weekday"])
    parts = []
    for (start, end, location), days in sorted(groups.items(), key=lambda item: (min(item[1]), item[0])):
        names = [DAYS[d] for d in sorted(days)]
        label = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        parts.append(f"{label} · {twelve(minutes(start))}–{twelve(minutes(end))}" + (f" · {location}" if location else ""))
    return "; ".join(parts)
