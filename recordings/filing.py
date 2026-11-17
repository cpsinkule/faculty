"""Filing a recording by the calendar, without a card (docs/decisions/recordings.md, RECORDINGS "Filing").

A recording is filed by where its START falls, as campus wall-clock time, with ten minutes of grace before an entry's start:

  * inside a student's APPOINTMENT (not cancelled)                      -> that enrollment, with the appointment by reference;
  * inside a section's CLASS meeting as the Calendar shows it           -> that section's class day;
  * inside a calendar entry (Faculty's or Google's) that names a current term's course -> that term;
  * inside an entry that names no course of Faculty's                   -> not Faculty's: NOT KEPT (the caller erases its list row);
  * no entry, or two that disagree                                      -> it waits for the instructor's Keep or Don't keep.

Calendar entries hold no attendees, so the title is all the evidence there is. Pure: the caller passes the calendar as the one feed
assembles it (scheduling/feed.py), each row's time read by the one engine (scheduling/engine.py).

Readings built in step 1, the instructor's to change (the contract names them):
  * AN ENTRY NAMES A TERM by its title holding a current term's course code ("MATH 140", "math140", "Math-140"), or, for one of
    Faculty's own entries, by belonging to a current term (an exam sitting, a custom entry scoped to the term).
  * OFFICE HOURS WITH NO APPOINTMENT wait: the block says a student came by, and nothing says who.
  * A DR booking and a due date are not places the instructor is; they file nothing. Nor does an entry that only says the
    instructor is away ("Unavailable", "Out of office"): it says where they are not. An all-day entry never counts.
  * NOT KEPT NEEDS THE WHOLE CALENDAR. The caller holds a not-Faculty's decision back (the recording waits, with the reason)
    while Google's calendar cannot vouch for that time: an automatic Not kept is final (nothing of the recording is held
    after it), and the entry that would have named the course may be one Faculty has not read yet.
  * What a link keeps of the entry it rests on (`basis`) is its id, source, date and times, and its title only where the entry
    names something of Faculty's: never an appointment's words, and never the title of an entry that made a recording Not
    Faculty's (a private entry's title has no place in a record that is kept).
  * An appointment and a class meeting at one time disagree; a term entry beside either agrees when it is the same term.
  * AN APPOINTMENT RECORDED AS CANCELLED (step 4) is the weakest evidence there is: the calendar does not show it, and the
    caller passes it (`cancelled` true) only so that a recording which began inside one, WITH NOTHING ELSE on the calendar at
    that time, is filed under that student and marked on its page (the appointment is recorded as one that did not happen,
    and Faculty changes neither). Any other entry at that time decides as if the cancelled appointment were not there. A
    no-show is on the calendar and files as any appointment does; the page marks it the same way.
"""
import datetime as dt
import re

from course_codes import split_course_code
from scheduling import engine
from scheduling.clock import CAMPUS_ZONE, parse_clock

GRACE = 10   # minutes before an entry's start that still count as inside it
CODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,10})[ \t-]{0,3}([0-9]{2,4})(?![A-Za-z0-9])")
LEGACY_CLASS = re.compile(r"·\s*Section\s+\d+\s*$")   # a per-date class event, as scheduling.service._legacy tells one
APPOINTMENT = "office-hours:"

NO_ENTRY = "No calendar entry at that time."
UNSEEN = "The only calendar entry at that time names no course of yours, but"   # ... and then why Google cannot vouch for it
CANCELLED = "It began inside a student's appointment that is recorded as cancelled, but"   # ... the same: nothing else was seen there
NO_START = "Plaud did not say when it began."
NOT_OURS = "The calendar entry at that time names no course of yours."   # said only of one the instructor asked to be asked about again
DROP_IN = "During office hours with no appointment. Pick the student."


def campus(instant):
    """The campus wall-clock time of a UTC instant, as a naive datetime (what calendar entries are written in)."""
    return instant.astimezone(CAMPUS_ZONE).replace(tzinfo=None)


def _at(day, minutes):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time()) + dt.timedelta(minutes=minutes)


def inside(event, moment):
    """Whether the campus wall-clock `moment` falls in a timed entry, from GRACE minutes before its start to its end."""
    found = engine.entry(event)
    if found is None:
        return False
    try:
        start = _at(found.date, found.start)
        if found.end_date > found.date:
            last = parse_clock(event.get("end_time"))
            end = _at(found.end_date, last if last is not None else 24 * 60)
        else:
            end = _at(found.date, found.end)
    except ValueError:
        return False
    return start - dt.timedelta(minutes=GRACE) <= moment < end


def named_terms(title, terms):
    """The current terms whose course code a title holds. `terms` is [{id, code, definition_id}]."""
    said = {" ".join(split_course_code(f"{prefix} {number}") or ()) for prefix, number in CODE.findall(str(title or ""))}
    return [term["id"] for term in terms if " ".join(split_course_code(term["code"]) or ("", "")) in said]


def _classify(event, terms):
    """What one entry holding the moment says: ("appointment", item id), ("class", section, term), ("office",), ("term", id) once
    for each term it names, ("other",) for an entry naming nothing of Faculty's, or nothing for an entry that files nothing."""
    source, title = event.get("source"), str(event.get("title") or "")
    if source == "office_hours":
        return [("appointment", str(event.get("id") or "").removeprefix(APPOINTMENT))]
    kind = engine.kind_of(event)
    if kind in ("dr", "deadline", "away"):
        return []
    if event.get("section_id") and event.get("term_id") and (kind == "class" or (source == "custom" and LEGACY_CLASS.search(title))):
        return [("class", event["section_id"], event["term_id"])]
    if kind == "office_hours":
        return [("office",)]
    current = {term["id"] for term in terms}
    if event.get("term_id") in current:
        return [("term", event["term_id"])]
    of_course = [term["id"] for term in terms if event.get("definition_id") and term["definition_id"] == event["definition_id"]]
    named = of_course if source != "google" and of_course else named_terms(title, terms)
    if named:
        return [("term", term_id) for term_id in named]
    return [("other",)]


def _shown(event, title=True):
    """What a link's basis keeps of an entry: its id, source, date and times, and its title unless `title` is False. An
    appointment never keeps its words: its title holds what the student came about."""
    kept = {key: str(event.get(key) or "") for key in ("id", "source", "date", "time", "end_time")}
    if title and event.get("source") != "office_hours":
        kept["title"] = str(event.get("title") or "")[:200]
    return kept


def _title(event):
    return "an appointment" if event.get("source") == "office_hours" else f"“{str(event.get('title') or 'an entry')[:80]}”"


def decide(moment, events, terms, appointments):
    """Where a recording that began at the campus wall-clock `moment` is filed: {target, detail, basis}. `target` is the link to
    write ({kind, ...owner}) or None when it waits, with `detail` saying why. `events` is the calendar feed around that day,
    `terms` the current terms, `appointments` {support item id: {enrollment_id, term_id}} for the appointments in `events`."""
    held = [(event, found) for event in events if inside(event, moment) for found in _classify(event, terms)]
    students, classes, named, office, others, called_off = {}, {}, {}, [], [], {}
    for event, found in held:
        if found[0] == "appointment" and found[1] in appointments:
            (called_off if event.get("cancelled") else students).setdefault(found[1], event)
        elif found[0] == "class":
            classes.setdefault((found[1], found[2]), event)
        elif found[0] == "term":
            named.setdefault(found[1], event)
        elif found[0] == "office":
            office.append(event)
        elif found[0] == "other":
            others.append(event)

    def waits(detail, *entries):
        return {"target": None, "detail": detail, "basis": {"entries": [_shown(entry) for entry in entries]}}

    def disagree(first, second):
        return waits(f"Two calendar entries at that time disagree: {_title(first)} and {_title(second)}.", first, second)

    specific = [("enrollment", key, event) for key, event in students.items()] + [("class_day", key, event) for key, event in classes.items()]
    if len(specific) > 1:
        return disagree(specific[0][2], specific[1][2])
    if specific:
        kind, key, event = specific[0]
        if kind == "enrollment":
            owner = appointments[key]
            target = {"kind": "enrollment", "enrollment_id": owner["enrollment_id"], "term_id": owner["term_id"], "appointment_id": key}
        else:
            target = {"kind": "class_day", "section_id": key[0], "term_id": key[1], "class_date": moment.date().isoformat()}
        other = next((entry for term_id, entry in named.items() if term_id != target["term_id"]), None)
        if other is not None:
            return disagree(event, other)
        return {"target": target, "detail": "", "basis": {"entries": [_shown(event)]}}
    if office:
        return waits(DROP_IN, office[0])
    if len(named) > 1:
        first, second = list(named.values())[:2]
        return disagree(first, second) if first is not second else waits(
            f"{_title(first)} names more than one current term. Pick the one it was.", first)
    if named:
        (term_id, event), = named.items()
        return {"target": {"kind": "term", "term_id": term_id}, "detail": "", "basis": {"entries": [_shown(event)]}}
    if others:
        return {"target": {"kind": "not_faculty"}, "detail": "", "basis": {"entries": [_shown(others[0], title=False)]}}
    if len(called_off) > 1:
        first, second = list(called_off.values())[:2]
        return disagree(first, second)
    if called_off:
        # Nothing else was on the calendar: the student may have come after all. Filed under them and marked; `weak` has the
        # caller hold it back while Google's calendar cannot vouch that nothing else was there.
        (key, event), = called_off.items()
        owner = appointments[key]
        return {"target": {"kind": "enrollment", "enrollment_id": owner["enrollment_id"], "term_id": owner["term_id"], "appointment_id": key},
                "detail": "", "basis": {"entries": [_shown(event)], "cancelled": True}, "weak": True}
    return waits(NO_ENTRY)
