"""SCHEDULING "ONE ENGINE" (stage 1b, 2026-10-05): what each calendar entry is, what it does to a new makeup or meeting, and the open
times left. Pure: callers pass the calendar as one feed assembles it (scheduling/feed.py); times are campus wall-clock minutes.

Before it, makeup cards (absences/makeup_slots.py), a reply's open meeting times and a card's calendar note (assistant/followups.py)
each decided "what blocks what" for themselves. Now one table does, `SHARING`: for a new thing of a kind, each existing entry's kind
either blocks it, shares with it (makeups sit together), hosts it (it happens inside office hours or meeting hours), or is free
(the instructor need not be there: a DR exam at the testing center, a due date; requirement, 2026-10-05). "room" means an office-hours
block hosts a makeup only in a room the term ticked as able to seat one, and blocks it otherwise."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .clock import parse_clock, parse_span

DEFAULT_MINUTES = 60  # an entry without an end lasts this long, unless it says its own `minutes` (a makeup: its quiz)
MAKEUP = re.compile(r"make[\s-]?up", re.I)
# AWAY (requirement, 2026-11-08): a timed entry
# whose title says the instructor is not there. Office hours stay a free-for-all for everything else (ABSENCES "MAKEUP BOOKING").
AWAY = re.compile(r"\b(?:away|out\s+of\s+(?:the\s+)?office|ooo|unavailable|not\s+available|no\s+office\s+hours?|"
                  r"office\s+hours?\s+(?:is\s+|are\s+)?cancell?ed|cancell?ed\s+office\s+hours?)\b", re.I)
KINDS = ("class", "office_hours", "meeting_hours", "makeup", "appointment", "exam", "dr", "deadline", "away", "other")
_ALL_BLOCK = {kind: "blocks" for kind in KINDS}
SHARING = {
    # A makeup shares with makeups, takes meeting hours, takes office hours only in a room that can seat it, never a meeting.
    "makeup": {**_ALL_BLOCK, "makeup": "shares", "meeting_hours": "hosts", "office_hours": "room", "dr": "free", "deadline": "free"},
    # A meeting (an office-hours appointment, a reply's offered time) happens in office or meeting hours and never over a makeup;
    # meetings share with one another (requirement, 2026-10-06).
    "appointment": {**_ALL_BLOCK, "office_hours": "hosts", "meeting_hours": "hosts", "appointment": "shares", "dr": "free",
                    "deadline": "free"},
}


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    date: str
    end_date: str
    start: int
    end: int
    title: str
    source: str
    room: str
    seats_makeup: bool | None = None  # a Term schedule's office hours say it themselves (stage 1d); else the term's ticked rooms decide


def is_makeup(event):
    """A makeup: a makeup sitting (the calendar marks it `makeup`) or any entry whose title says makeup."""
    return bool(event.get("makeup")) or bool(MAKEUP.search(str(event.get("title") or "")))


def is_away(event):
    """A time the instructor is not there: a timed entry whose title says so ("Away", "Unavailable", "Out of office", "Office
    hours cancelled"). Read from the title alone, as office hours and makeups are; a student's appointment never is one."""
    return (event.get("source") != "office_hours" and not event.get("all_day")
            and bool(AWAY.search(str(event.get("title") or ""))))


def is_office_block(event):
    """One of the instructor's office-hours blocks: their own timed event saying "office hour" (a student's appointment is not, nor
    an entry saying they are away, "Office hours cancelled")."""
    return (event.get("source") != "office_hours" and not event.get("all_day") and parse_clock(event.get("time")) is not None
            and "office hour" in str(event.get("title") or "").casefold() and not is_away(event))


def kind_of(event):
    """The one mapping from a calendar row to a kind."""
    if event.get("source") == "schedule" and event.get("schedule_kind") in KINDS:
        return event["schedule_kind"]
    if event.get("source") == "office_hours":
        return "appointment"
    if is_away(event):  # before makeup: "Unavailable for makeups" is time away, never a makeup to share
        return "away"
    if is_makeup(event):
        return "makeup"
    if event.get("source") == "dr":
        return "dr"
    if event.get("category") == "deadline" or event.get("source") == "gradebook":
        return "deadline"
    if "office hour" in str(event.get("title") or "").casefold():
        return "office_hours"
    if event.get("source") in ("exam", "seating"):
        return "exam"
    return "other"


def span(event):
    """(start, end) minutes of a timed entry on its own day, or None. The time is read as typed (stage 1g, 2026-10-05: "7:05PM", a
    range "9:00 AM–10:15 AM" with its end; a makeup also from loose text, "around 2 PM"); the end is its `end_time`, else the typed
    range's end, else its `minutes` (a makeup sitting: its quiz), else DEFAULT_MINUTES."""
    if event.get("all_day"):
        return None
    typed = parse_span(event.get("time"))
    start, typed_end = typed if typed else (parse_clock(event.get("time"), "loose") if is_makeup(event) else None, None)
    if start is None:
        return None
    end = parse_clock(event.get("end_time"), "any")
    end = end if end is not None and end > start else typed_end
    length = event.get("minutes") if isinstance(event.get("minutes"), int) and event.get("minutes") > 0 else DEFAULT_MINUTES
    return start, end if end is not None and end > start else min(start + length, 24 * 60)


def entry(event):
    """The engine's view of a calendar row, or None when it has no readable time (all-day entries never take time)."""
    found = span(event)
    if found is None:
        return None
    return Entry(str(event.get("id") or ""), kind_of(event), event.get("date") or "", event.get("end_date") or event.get("date") or "",
                 found[0], found[1], str(event.get("title") or ""), str(event.get("source") or ""),
                 " ".join(str(event.get("location") or "").split()),
                 event["seats_makeup"] if isinstance(event.get("seats_makeup"), bool) else None)


def room_allows(rooms, location):
    """Whether a room is one the term ticked as able to seat a makeup (case and spacing ignored)."""
    key = " ".join(str(location or "").split()).casefold()
    return bool(key) and key in {" ".join(str(room).split()).casefold() for room in rooms}


def effect(new_kind, found, rooms=()):
    """What an existing entry does to a new thing of `new_kind`: "blocks", "shares", "hosts" or "free"."""
    rule = SHARING[new_kind].get(found.kind, "blocks")
    if rule == "room":
        seats = found.seats_makeup if found.seats_makeup is not None else room_allows(rooms, found.room)
        return "hosts" if seats else "blocks"
    return rule


def _on_day(found, event, day):
    """The timed part of an entry on one campus date, including multi-day entries."""
    if found is None or not found.date <= day <= found.end_date:
        return None
    if found.date == found.end_date:
        return found.start, found.end
    last = parse_clock(event.get("end_time"))
    return (found.start if day == found.date else 0,
            (last if last is not None else 24 * 60) if day == found.end_date else 24 * 60)


def away(events, day):
    """[(start, end)] minutes of `day` the instructor's calendar says they are away (AWAY): the one thing that takes time out of an
    office-hours block offered as "come any time"."""
    iso = day.isoformat()
    return [period for period in (_on_day(entry(event), event, iso) for event in events if is_away(event)) if period is not None]


def busy(events, day, new_kind, *, exclude=(), rooms=()):
    """[(start, end)] minutes of `day` that the calendar takes from a new thing of `new_kind` (entries over several days take their
    first day from their start, their last to their end, the days between wholly)."""
    taken, iso = [], day.isoformat()
    for event in events:
        if event.get("id") in exclude:
            continue
        found = entry(event)
        period = _on_day(found, event, iso)
        if period is not None and effect(new_kind, found, rooms) == "blocks":
            taken.append(period)
    return taken


def clear(taken, start, end):
    return not any(s < end and start < e for s, e in taken)


def open_starts(low, high, taken, *, length, step=30, allowed=lambda start: True):
    """Starts every `step` minutes from `low` whose whole [start, start + length) is clear and ends by `high`."""
    return [start for start in range(low, high, step) if start + length <= high and allowed(start) and clear(taken, start, start + length)]


def free_spans(low, high, taken, *, minimum=30):
    """The clear stretches of [low, high), each at least `minimum` minutes."""
    free, cursor = [], low
    for start, end in sorted(taken):
        if end <= cursor or start >= high:
            continue
        if start - cursor >= minimum:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if high - cursor >= minimum:
        free.append((cursor, high))
    return free


def check(events, *, new_kind, date, start, end, own=None, rooms=()):
    """What the calendar holds at a proposed time: {"overlaps": entries that block it, "within": entries that host or share it}."""
    overlaps, within = [], []
    for event in events:
        if own and event.get("id") == own:
            continue
        found = entry(event)
        period = _on_day(found, event, date)
        if period is None or not (period[0] < end and start < period[1]):
            continue
        rule = effect(new_kind, found, rooms)
        if rule == "blocks":
            overlaps.append(found)
        elif rule in ("hosts", "shares"):
            within.append(found)
    return {"overlaps": overlaps, "within": within}
