"""CHECKED AGAINST FACULTY, by code and never by a model (docs/decisions/recordings.md, RECORDINGS "The reading (step 3)").

Where an item of a reading concerns something Faculty holds, the page says what Faculty FOUND in its own records: a seating
chart built after the last drop, no drops set, a published page in the Canvas copy, the testing center's bookings by day, an
entry on the calendar. Each is read from the owning module's own tables or service, never copied, and never written: "already
done" is a VIEW, recomputed at each read of the page.

`found(db, item, context)` answers `{state, text, href}` or None when the item concerns nothing Faculty can look up:
  done    Faculty's records say it is already so;
  number  a ready number the instructor was asked for;
  open    Faculty's records do not show it (yet);
  listed  the instructor's own Google entries that a called-off meeting concerns (Faculty never changes those: CALENDAR);
  info    what Faculty holds, said plainly, where "done" is not Faculty's to judge.
`offer` is "calendar" where a dated thing is not on the calendar, so a card may propose the entry.

A check is told what it needs and reads nothing else: no student is named by any of them (a testing center booking is counted,
an appointment on the calendar reads "an appointment").
"""
import datetime as dt
import json
import re
import sqlite3

from scheduling import engine
from scheduling.clock import CAMPUS_ZONE, day_label, display_clock, parse_clock

from . import filing, reading

NEAR = 45     # minutes: an entry this close to a time that was said is that thing on the calendar
MEETINGS = ("other", "exam")   # what a meeting, a session or an exam is on the calendar (scheduling.engine.kind_of)
SERIES = 150  # days: how far ahead the later entries of a meeting's own series are looked for when it is called off


def class_meeting(event):
    """A section's class meeting on the calendar: the class schedule's own, or a per-date class event made by hand for one
    section ("<code> · Section N": a redefined day; `filing.LEGACY_CLASS`, as step 1 files a class by one). Second live
    reading, 2026-11-13: a called-off meeting listed the four class events made by hand for a redefined day."""
    return engine.kind_of(event) == "class" or bool(
        event.get("source") == "custom" and event.get("section_id") and filing.LEGACY_CLASS.search(str(event.get("title") or "")))


def _day(stamp):
    """The campus day of one of Faculty's UTC timestamps, as "Mon, Nov 9"; '' when it cannot be read."""
    try:
        moment = dt.datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return day_label((moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)).astimezone(CAMPUS_ZONE).date())


def _count(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def seating(db, term_id, assignment_id, title):
    """Seating charts for one assessment of the term (the classrooms'; a testing center chart is not one), against the last drop
    Faculty has on record."""
    charts = db.execute("SELECT generated_at FROM seating_charts WHERE term_id=? AND assignment_id=? AND dr_exam_key=''", (term_id, assignment_id)).fetchall()
    built = sorted(chart["generated_at"] for chart in charts if chart["generated_at"])
    if not built:
        return {"state": "open", "text": f"Faculty has no seating chart built for {title} yet.", "href": "#seating"}
    last = db.execute("SELECT MAX(d.dropped_at) FROM roster_dropped d JOIN course_sections s ON s.id=d.section_id WHERE s.term_id=?", (term_id,)).fetchone()[0]
    before = [stamp for stamp in built if last and stamp < last]
    if before:
        return {"state": "open", "href": "#seating",
                "text": f"{len(before)} of {_count(len(built), 'seating chart')} for {title} {'was' if len(before) == 1 else 'were'} built before the last drop Faculty has ({_day(last)})."}
    after = f", after the last drop Faculty has ({_day(last)})" if last else "; Faculty has no drop on record"
    return {"state": "done", "href": "#seating", "text": f"Already done: {_count(len(built), 'seating chart')} for {title}, the last built {_day(built[-1])}{after}."}


def gradebook(db, term_id):
    """Whether the term's gradebook drops any lowest scores."""
    row = db.execute("SELECT setup_json FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
    categories = (json.loads(row["setup_json"]).get("categories") or []) if row else []
    href = f"#gradebook/{term_id}"
    if not categories:
        return {"state": "info", "text": "The gradebook has no categories yet, so no drops are set.", "href": href}
    drops = [f"the lowest {category['drop_lowest']} in {category.get('name') or 'a category'}" for category in categories
             if isinstance(category.get("drop_lowest"), int) and category["drop_lowest"] > 0]
    return {"state": "info", "href": href, "text": f"The gradebook drops {', '.join(drops)}." if drops else "No drops are set in the gradebook."}


def _canvas(db, term_id):
    """[(kind, name, published)] of the modules, pages and assignments in Faculty's copy of the term's Canvas course, or None
    when Faculty holds no copy."""
    row = db.execute("SELECT content_json FROM canvas_content_revisions WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
    if not row:
        return None
    content = json.loads(row["content_json"])
    named = [("module", module.get("name"), module.get("published")) for module in content.get("modules") or []] + \
            [("page", page.get("title"), page.get("published")) for page in content.get("pages") or []] + \
            [("assignment", item.get("name"), item.get("published")) for item in content.get("assignments") or []]
    return [(kind, " ".join(str(name).split()), bool(published)) for kind, name, published in named if name and str(name).strip()]


def canvas_names(db, term_id, most=80):
    """The names a reading is told, so an item about Canvas can name the thing as the copy does: {modules, pages, assignments},
    each at most `most` names. Names of course material only; {} when there is no copy."""
    named = _canvas(db, term_id) or []
    return {f"{kind}s": [name[:160] for found, name, _ in named if found == kind][:most] for kind in ("module", "page", "assignment")} if named else {}


def canvas(db, term_id, words):
    """Whether Faculty's copy of the Canvas modules and pages holds something called `words`, and whether it is published there.
    A name that holds every word is the thing; when none does, the closest names are said and nothing is claimed."""
    named = _canvas(db, term_id)
    if named is None:
        return {"state": "info", "text": "Faculty has no copy of this term's Canvas modules and pages to look in.", "href": ""}
    wanted = [word for word in " ".join(str(words).split()).casefold().split() if word]
    held = [(sum(word in name.casefold() for word in wanted), kind, name, published) for kind, name, published in named]
    hits = [(kind, name, published) for count, kind, name, published in held if wanted and count == len(wanted)]
    if not hits:
        close = sorted((entry for entry in held if entry[0] >= 2 and entry[0] * 2 > len(wanted)), key=lambda entry: (-entry[0], len(entry[2])))[:2]
        if close:
            said = ", ".join(f"the {kind} “{name}” ({'published' if published else 'not published'})" for _, kind, name, published in close)
            return {"state": "info", "text": f"Nothing in Faculty's Canvas copy is called “{words}”. Closest: {said}.", "href": ""}
        return {"state": "open", "text": f"Nothing in Faculty's Canvas copy is called “{words}”.", "href": ""}
    kind, title, published = sorted(hits, key=lambda hit: (not hit[2], len(hit[1])))[0]
    more = f" ({_count(len(hits) - 1, 'other match')})" if len(hits) > 1 else ""
    if published:
        return {"state": "done", "text": f"Already done: the {kind} “{title}” is published in Faculty's Canvas copy{more}.", "href": ""}
    return {"state": "open", "text": f"The {kind} “{title}” is in Faculty's Canvas copy, not published{more}.", "href": ""}


def testing_center(db, term_id, assignment_id, title, code):
    """The testing center's bookings for one assessment of the term, by day: a ready number."""
    from workspace_calendar import dr_schedule_href
    days = db.execute("""SELECT json_extract(v.snapshot_json,'$.date') day, COUNT(*) bookings FROM dr_exam_bookings b
        JOIN dr_exam_revisions v ON v.id=b.revision_id JOIN dr_exam_assignment_links l ON l.booking_id=b.id
        JOIN course_sections s ON s.id=b.section_id WHERE s.term_id=? AND l.assignment_id=? GROUP BY day ORDER BY day""", (term_id, assignment_id)).fetchall()
    href = dr_schedule_href(code, title)
    if not days:
        return {"state": "open", "text": f"Faculty has no testing center booking for {title}.", "href": href}
    said = " · ".join(f"{day_label(dt.date.fromisoformat(row['day']))}: {row['bookings']}" if row["day"] else f"no day: {row['bookings']}" for row in days)
    return {"state": "number", "text": f"Testing center bookings for {title}: {said}.", "href": href}


def _entry(event):
    clock = display_clock(event.get("time")) if event.get("time") and not event.get("all_day") else ""
    title = "an appointment" if event.get("source") == "office_hours" else f"“{str(event.get('title') or 'an entry')[:80]}”"
    return f"{title}{' ' + clock if clock else ''}"


def ours(terms, basis_title):
    """Whether a calendar entry is this course's: the meeting's own entry (the title the recording was filed by), one naming a
    current term's course, or one Faculty holds for such a term."""
    mine, own = {term["id"] for term in terms}, " ".join(str(basis_title or "").split()).casefold()

    def mine_or_named(event):
        return bool((own and " ".join(str(event.get("title") or "").split()).casefold() == own)
                    or filing.named_terms(event.get("title"), terms) or event.get("term_id") in mine)
    return mine_or_named


def _called_off(entries, when, classes=False):
    """What the calendar still holds of a meeting said not to happen. The instructor's own Google entries are never edited
    (CALENDAR): they are listed, and removing them is theirs. With no such entry there is nothing to remove; where the course
    has CLASS meetings that day the page says they were not looked at, since it may be a class that was called off (step 5)."""
    if not entries and classes:
        return {"state": "info", "href": "#calendar",
                "text": f"No meeting of this course is on your calendar {when}. Your class meetings and office hours that day are not looked at here."}
    if not entries:
        return {"state": "done", "text": f"Nothing to remove: no meeting of this course is on your calendar {when}.", "href": "#calendar"}
    google = sum(1 for event in entries if event.get("source") == "google")
    shown = ", ".join(f"{day_label(dt.date.fromisoformat(event['date']))} {_entry(event)}" for event in entries[:8])
    more = f" and {len(entries) - 8} more" if len(entries) > 8 else ""
    note = " Faculty does not change your Google entries: removing them is yours." if google else ""
    return {"state": "listed", "href": "#calendar", "entries": len(entries),
            "text": f"Still on your calendar ({len(entries)} {'entry' if len(entries) == 1 else 'entries'}): {shown}{more}.{note}"}


def _words(text, apart=()):
    return [word for word in re.findall(r"[a-z0-9]+", str(text or "").casefold()) if word not in apart]


def _named(event, wanted):
    """Whether a calendar entry is named like the thing: its title holds more than half of the words it was called by (a word
    and its plural are one word)."""
    title = _words(event.get("title"))
    same = lambda word, other: word == other or (min(len(word), len(other)) >= 4 and (word.startswith(other) or other.startswith(word)))  # noqa: E731
    return bool(wanted) and sum(any(same(word, other) for other in title) for word in wanted) * 2 > len(wanted)


def calendar(item, feed, terms, basis_title, recorded, named=""):
    """A dated item against the calendar. `feed(first, last)` answers the one feed's rows between two dates, `terms` the current
    terms ([{id, code, definition_id}]), `basis_title` the title of the entry the recording was filed by (the meeting's own
    entry), `recorded` the recording's campus date. Three cases: something on a day said NOT to happen (the course's meetings
    the calendar still holds that day); the meetings of this kind called off from now on, no day said (the later entries of
    the meeting's own series); something said to happen on a day (whether the calendar holds it). Only what a MEETING is on
    the calendar counts (`MEETINGS`): a class meeting, office hours, time away, an appointment, a makeup and a testing center
    booking are never the thing a meeting spoke of (first live reading, 2026-11-13: a called-off meeting listed that day's
    four class meetings and office hours).

    Something said to happen is ON the calendar only by evidence: an entry of the course near the time that was said, or, with
    no time said, one NAMED like it (`named`: the words the reading called it by, or the title of the assessment it is about;
    the course's own code apart). Another entry of the course on that day is not the thing (second live reading, 2026-11-13: a
    dated item was "already done" because three homework deadlines fell on its day). With neither a time nor a name, the day's
    entries are shown and nothing is claimed."""
    ours_ = ours(terms, basis_title)
    meeting = lambda event, kinds=MEETINGS: engine.kind_of(event) in kinds and not class_meeting(event) and ours_(event)  # noqa: E731
    if not item["date"]:
        if item["happening"] or not basis_title:
            return None
        own = " ".join(basis_title.split()).casefold()
        later = [event for event in feed(recorded + dt.timedelta(days=1), recorded + dt.timedelta(days=SERIES))
                 if engine.kind_of(event) in MEETINGS and not class_meeting(event) and " ".join(str(event.get("title") or "").split()).casefold() == own
                 and event.get("date", "") > recorded.isoformat()]
        return _called_off(sorted(later, key=lambda event: (event["date"], event.get("time") or "")), "after this meeting")
    day = dt.date.fromisoformat(item["date"])
    that_day = [event for event in feed(day - dt.timedelta(days=1), day)
                if event.get("date", "") <= item["date"] <= (event.get("end_date") or event.get("date", ""))]
    if not item["happening"]:
        return _called_off(sorted((event for event in that_day if meeting(event)), key=lambda event: event.get("time") or ""),
                           f"on {day_label(day)}", classes=any(class_meeting(event) and ours_(event) for event in that_day))
    said = parse_clock(item.get("time") or "")
    course = [event for event in that_day if meeting(event, MEETINGS + ("deadline",))]
    wanted = _words(named, apart={word for term in terms for word in _words(term.get("code"))})
    shown = lambda entries: ", ".join(_entry(event) for event in entries[:3])  # noqa: E731
    if said is not None:
        held = [event for event in course if (span := engine.span(event)) and span[0] - NEAR <= said <= span[0] + NEAR]
    elif wanted:
        held = [event for event in course if _named(event, wanted)]
    elif course:
        return {"state": "info", "href": "#calendar",
                "text": f"On your calendar that day: {shown(course)}. Faculty cannot tell whether one of them is this."}
    else:
        held = []
    if held:
        return {"state": "done", "text": f"On your calendar that day: {shown(held)}.", "href": "#calendar"}
    if course and said is None:
        return {"state": "open", "text": f"Nothing named like this is on your calendar on {day_label(day)}.", "href": "#calendar", "offer": "calendar"}
    return {"state": "open", "text": f"Not on your calendar on {day_label(day)}.", "href": "#calendar", "offer": "calendar"}


def by_name(name, assessments, code):
    """What is said of the grading of an assessment the reading had no listing for, held under the NAME the meeting called it
    by: the assessment of this term that name is (`reading.named`; `assessments` is {id: title}), or that none is yet. It is
    offered at that assessment's rubric, also one made after the meeting."""
    titles = sorted({assessments[identity] for identity in reading.named(name, assessments, apart=reading.name_words(code))})
    if titles:
        return {"state": "info", "href": "", "text": f"Held for {' and '.join(f'“{title}”' for title in titles)}: matched by the name it was called by, “{name}”."}
    return {"state": "info", "href": "",
            "text": f"No assessment of this term is called “{name}”. This is offered at its rubric's finalization once one is."}


def found(db, item, context):
    """What Faculty's own records say of one item, or None. `context`: {term_id, code, assessments: {id: title}, terms, basis_title,
    recorded: the recording's campus date, feed: (first, last) -> the calendar feed's rows}. A record that cannot be read answers
    None: nothing is guessed."""
    about, term_id = item.get("about") or {}, context.get("term_id")
    what, title = about.get("what"), (context.get("assessments") or {}).get(about.get("assessment"), "")
    try:
        if what == "seating" and term_id and title:
            return seating(db, term_id, about["assessment"], title)
        if what == "testing_center" and term_id and title:
            return testing_center(db, term_id, about["assessment"], title, context.get("code") or "")
        if what == "gradebook" and term_id:
            return gradebook(db, term_id)
        if what == "canvas" and term_id and about.get("words"):
            return canvas(db, term_id, about["words"])
        if what == "calendar" or item.get("kind") == "date":
            said = calendar(item, context["feed"], context.get("terms") or [], context.get("basis_title") or "", context["recorded"],
                            named=about.get("words") or title)
            if said and what != "calendar":
                # A date of something a record owns (an exam's day, a rubric's deadline) is shown against the calendar and never
                # offered as a new entry: that day belongs to its own record.
                said.pop("offer", None)
            return said
        if (item.get("kind") == "ruling" or what == "rubric") and what in ("assessment", "rubric") and not about.get("assessment") and about.get("words"):
            return by_name(about["words"], context.get("assessments") or {}, context.get("code") or "")
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return None
    return None
