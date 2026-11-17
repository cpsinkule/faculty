"""The times a quiz makeup booking card offers (ABSENCES "MAKEUP BOOKING"; requirement, 2026-10-04).
2026-10-06, NEXT TWO DAYS: "The makeup cards should be changed to suggest available times in the next 2 days instead of just monday
and tuesday"; asked, they chose the next two days that have an open time (today too, while a time is still LEAD away) and never
before the quiz: from now or the day after the absence ends, whichever is later. This replaced the week after the quiz.
2026-10-30, FROM THEIR RETURN: "fix it so that the times offered correspond to their return date, not the date of the absence (of
course, if the followup is after their return date it shouldn't backdate it either)": never before the day the student can return
(the "return(s) <Month D>" the absence's details name after its last day, absences/service.py `return_date`, the department makeup
request's rule), and the due date's 30 days count from that day when it is later than the absence's last day.

Pure: the caller passes the calendar as the Calendar page reads it (`workspace_calendar` rows: Faculty's own events, the instructor's
Google events, exam sittings, appointments), the term's meeting hours and makeup rooms, the student's quiz length and the campus
wall-clock time now; nothing is read or written here.

Starting at the latest of today, the day after the absence ends (or after the quiz was given, if later), the day the student can
return (`returns`) and the day after `after` (the last day a student's earlier card offered), day by day, the first DAYS days that have at least one of these:
- the instructor's office-hours blocks that day whose room the term ticked as able to seat a makeup (`rooms`; never their small
  office): their own calendar events whose title says "office hour" (the rule the follow-up assistant uses; a student's office-hours
  appointment is not a block), each offered as itself, less any time a timed entry says they are away (scheduling.engine AWAY:
  "Away", "Unavailable", "Out of office"), and not at all when what is left cannot hold the student's quiz;
- the term's meeting hours on that weekday (`windows`, student_support/meeting_hours.py), one start every 30 minutes in the window's
  room, kept only when the student's whole quiz (`minutes`: 20 times their extra time) fits before the window ends and nothing timed
  on the calendar overlaps it but other makeups (several students may share a time) and all-day entries.
A time that starts less than LEAD from now, falls inside the student's absence, or comes after the makeup's due date (WINDOW days after
the absence's last day, or after the student's return when that is later; the term policy) is left out; a day left with nothing does not count. The due date ends the search.
Dates and times are campus wall-clock values, compared as written."""
from __future__ import annotations

import datetime as dt

from scheduling import engine
from scheduling.clock import day_label, hhmm, long_day, parse_clock, span_text as span, twelve  # noqa: F401 (this module's helpers)
from scheduling.engine import DEFAULT_MINUTES as DEFAULT_LENGTH, MAKEUP, is_makeup, is_office_block, room_allows  # noqa: F401

DAYS = 2  # the first two days that have an open time, so a makeup is not put off (requirement, 2026-10-06)
STEP = 30  # a meeting-hours start every 30 minutes
QUIZ_MINUTES = 20  # a quiz's length for scheduling, before a student's extra time (absences/makeup_length.py)
LEAD = dt.timedelta(hours=3)  # a time this close is left out: its copy has to be printed first
WINDOW = 30  # calendar days after the absence's last day by which the makeup must be done (term policy, quiz makeups)
FIELD, OTHER_FIELD = "makeup_when", "makeup_other"
OTHER = "None of these work: I need another time"


def clock(value):
    """'15:00' → 900 minutes, or None for anything else (scheduling.clock "hhmm")."""
    return parse_clock(value, "hhmm")


def due_date(last_date, returns=None):
    """The day the makeup must be done by: WINDOW calendar days after the absence's last day, or after the day the student can
    return (`returns`) when that is later (FROM THEIR RETURN, 2026-10-30: a late return never shrinks the window)."""
    return (dt.date.fromisoformat(max(last_date, returns or "")) + dt.timedelta(days=WINDOW)).isoformat()


def loose_clock(value):
    """A start read from a makeup's time as typed before Schedule makeup asked for an exact one (scheduling.clock "loose")."""
    return parse_clock(value, "loose")


def shareable(event):
    """A calendar entry a makeup time may overlap: another makeup (scheduling.engine SHARING; requirement, 2026-10-05)."""
    return is_makeup(event)


def event_span(event):
    """(start, end) minutes of a timed entry on its own day, or None (scheduling.engine.span)."""
    return engine.span(event)


def _day(day, events, first_date, last_date, due, earliest, exclude, windows, rooms, minutes):
    """One date's options, in time order: its office-hours blocks in a ticked room and its meeting-hours starts."""
    date = day.isoformat()
    if first_date <= date <= last_date or date > due:
        return []

    def allowed(start):
        return dt.datetime.combine(day, dt.time(start // 60, start % 60)) >= earliest

    options, seen, away = [], set(), engine.away(events, day)
    for event in sorted((e for e in events if is_office_block(e) and e.get("date") == date), key=lambda e: e.get("time") or ""):
        place = " ".join(str(event.get("location") or "").split())
        if not room_allows(rooms, place):
            continue
        start = clock(event["time"])
        end = clock(event.get("end_time"))
        end = end if end is not None and end > start else start + DEFAULT_LENGTH
        # AWAY: only time the instructor's calendar says they are away comes out of a block, and what is left is offered when the
        # student's whole quiz fits in it (requirement, 2026-11-08: a doctor's appointment from 3 PM on a Friday).
        for start, end in engine.free_spans(start, end, away, minimum=minutes) if away else [(start, end)]:
            key = (start, end, place)
            if key in seen or not allowed(start):
                continue
            seen.add(key)
            text = f"{day_label(day)} · {span(start, end)}{f' · {place}' if place else ''} · office hours, come any time"
            options.append({"text": text[:200], "kind": "office_hours", "date": date, "time": hhmm(start), "end_time": hhmm(end),
                            "location": place, "minutes": minutes})
    # Meeting hours keep the kind "afternoon" the first cards were saved with (a time the student picks, in a window's room); an
    # option's end_time is its half-hour step (how the follow-up words the window), `minutes` its quiz length.
    for window in sorted((w for w in windows if w["weekday"] == day.weekday()), key=lambda w: w["start"]):
        place = window["location"]
        taken = engine.busy(events, day, "makeup", exclude=exclude, rooms=rooms)
        close = clock(window["end"])
        for start in engine.open_starts(clock(window["start"]), close, taken, length=minutes, step=STEP, allowed=allowed):
            options.append({"text": f"{day_label(day)} · {twelve(start)}{f' · {place}' if place else ''}", "kind": "afternoon",
                            "date": date, "time": hhmm(start), "end_time": hhmm(min(start + STEP, close)), "location": place,
                            "minutes": minutes})
    options.sort(key=lambda option: option["time"])
    return options


def offer(given_date, first_date, last_date, events, now, *, after=None, exclude=(), windows=(), rooms=(), minutes=QUIZ_MINUTES, due=None,
          returns=None):
    """What one student's card offers: {"week": the first day offered (None when no time is left before the due date; the name of
    the offer's column, which held the week's Monday before 2026-10-06), "due": the due date, "options": [{text, kind, date, time,
    end_time, location, minutes}] in time order}. `now` is the campus wall-clock time (a naive datetime); `after` is the last day an
    earlier card offered ("Send a new card"); `exclude` names calendar rows that never make a time busy (the makeup sittings
    themselves); `windows` are the term's meeting hours and `rooms` the office-hours rooms that can seat a makeup
    (student_support/meeting_hours.py); `minutes` is the student's quiz length; `due` replaces the policy's due date (one a later card
    moved, ABSENCES "MAKEUP BOOKING" › A MISSED MAKEUP); `returns` is the day the student can return, when known (FROM THEIR
    RETURN: no time before it, and never before now either, so a card made after that day starts from today)."""
    due, exclude, earliest = due or due_date(last_date, returns), set(exclude), now + LEAD
    day = max(now.date(), dt.date.fromisoformat(max(given_date, last_date)) + dt.timedelta(days=1))  # never before the quiz
    if returns:
        day = max(day, dt.date.fromisoformat(returns))
    if after:
        day = max(day, dt.date.fromisoformat(after) + dt.timedelta(days=1))
    days, options = [], []
    while len(days) < DAYS and day.isoformat() <= due:
        found = _day(day, events, first_date, last_date, due, earliest, exclude, windows, rooms, minutes)
        if found:
            days.append(day.isoformat())
            options.extend(found)
        day += dt.timedelta(days=1)
    return {"week": days[0] if days else None, "due": due, "options": options}


def choices(options):
    """The card's option list: each time's text, then "None of these work"."""
    return [option["text"] for option in options] + [OTHER]
