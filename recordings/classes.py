"""Recordings, step 5: A CLASS as its section's class day sees it, BY REFERENCE (docs/decisions/recordings.md, RECORDINGS
"Classes (step 5)"; docs/decisions/scheduling.md, SCHEDULING "CLASS SCHEDULE: WHAT A RECORDING REACHED").

A recording filed on a section's class day belongs to that day and to no student. Its reading keeps two kinds of item and no
other: `covered` (a notes section the class worked through, by its NUMBER, as the class schedule's topics name one) and
`announced` (a day the instructor told the class). Everything here is CODE, never a model, and a READ: nothing is copied to
the class schedule, and the plan never changes by itself.

  * COVERED AGAINST PLANNED (`compare`): what the day's recordings reached beside the topics planned for that section's day.
    They DIFFER where a section planned for the day was not reached, on that day or on an earlier recorded class day of the
    section. What was reached beyond the plan is shown and is not a difference: nothing of this day needs moving for it. A day
    with no topics planned, or whose reading kept no `covered` item, is set against nothing.
  * THE MOVE (`move`): the class-schedule change that takes what was not reached off this section's day and puts it first on
    its next class day, as that section's own topics (other sections may have reached more). It is only ever proposed, on ONE
    Approve card through the class schedule's own route (recordings/readings.py); a declined card is the instructor's word
    that the plan stands.
  * ANNOUNCED AGAINST THE RECORDS (`announced`): a day told to the class against what the section's class schedule holds for
    that work (the day it is given, its due date in Faculty's Canvas copy or the gradebook, a planned row) or for the class
    itself (whether it meets that day). Where they disagree the item says both.

`recorded` is what the class schedule's own read shows on a class day (scheduling/class_schedule.py `section_schedule`).
"""
import json
from datetime import date

from scheduling.clock import day_label, display_clock

from .service import LINKS

SOURCE = {"seating": "its seating chart", "exam": "its exam record", "planned": "the class schedule's plan",
          "canvas": "Faculty's Canvas copy", "gradebook": "the gradebook"}
VERB = {"given": "given", "date": "on", "due": "due"}


def tables(db):
    return {"recording_links", "recording_readings", "recording_item_readings", "recording_item_events"} <= {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'recording_%'")}


def label(day):
    return day_label(date.fromisoformat(day))


def listed(words):
    """"5.6", "5.6 and 5.7", "5.6, 5.7 and 5.8"."""
    words = [str(word) for word in words]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}" if words else ""


class Term:
    """What one read needs of a term's class schedule, each part loaded when first asked for and kept for that read alone
    (PERFORMANCE: a memo lasts one read). `read` is the class schedule's own memo of the term when the caller holds one."""

    def __init__(self, db, term_id, read=None):
        self.db, self.term_id, self._read = db, term_id, read
        self._plan = self._notes = self._row = None
        self._reached, self._meetings, self._cards, self._numbers = {}, {}, {}, {}

    @property
    def read(self):
        if self._read is None:
            from scheduling.class_schedule import TermRead
            self._read = TermRead(self.db, self.term_id)
        return self._read

    @property
    def plan(self):
        if self._plan is None:
            from scheduling.class_schedule import plan
            self._plan = self._read.plan if self._read is not None else plan(self.db, self.term_id)
        return self._plan

    @property
    def row(self):
        if self._row is None:
            found = self.db.execute("SELECT id, code, term, term_other, year, definition_id FROM course_terms WHERE id=?", (self.term_id,)).fetchone()
            self._row = dict(found) if found else {}
        return self._row

    @property
    def notes(self):
        """{a notes section's number: its title}, as the course's notes outline has them now."""
        if self._notes is None:
            from course_notes.service import current_sections
            found = self._read.notes if self._read is not None else current_sections(self.db, self.row.get("definition_id"))
            self._notes = {number: entry.get("title") or "" for number, entry in found.items()}
        return self._notes

    def number(self, section_id):
        if section_id not in self._numbers:
            found = self.db.execute("SELECT number FROM course_sections WHERE id=?", (section_id,)).fetchone()
            self._numbers[section_id] = found[0] if found else ""
        return self._numbers[section_id]

    def href(self, section_id):
        """The section's Class schedule page."""
        from course_groups import navigation_id
        if not self.row:
            return ""
        return f"#course/{navigation_id(self.row)}/section/{self.number(section_id)}/schedule"

    def topics(self, section_id, day):
        """The topics planned for a section's day: its own entry where it has one, else the term's; None when neither says
        anything (scheduling.class_schedule.TermRead.topics, without the titles)."""
        entry = self.plan["topics"].get(day)
        if not entry:
            return None
        own = entry.get("sections", {}).get(section_id)
        chosen = own or entry
        if not chosen["notes"] and not chosen["note"]:
            return None
        return {"notes": list(chosen["notes"]), "note": chosen["note"], "own": bool(own)}

    def meetings(self, section_id):
        """({date: [{time, end_time, room}]}, {days off}) of a section, as its class schedule shows them."""
        if section_id not in self._meetings:
            from scheduling.class_schedule import section_meetings
            self._meetings[section_id] = self._read.meetings(section_id) if self._read is not None \
                else section_meetings(self.db, self.term_id, section_id)
        return self._meetings[section_id]

    def reached(self, section_id):
        """{class date: {recordings: [ids, in the order they were filed], heard: [the ones whose reading kept a section, in
        that order], read: whether any has a reading, numbers: the notes sections its readings kept as covered, in the
        order said, items: {number: [item ids]}, at: {number: (recording, milliseconds in)}}} for every class day of the
        section a recording is filed on now. A `covered` item the instructor DISMISSED is the instructor saying it was not
        so: its sections are not reached by it. The section's own links are read first and only their readings after,
        so a section with no class recording costs one small query."""
        if section_id in self._reached:
            return self._reached[section_id]
        from .meetings import settled
        from .readings import CURRENT
        days = {}
        rows = [dict(row) for row in self.db.execute(
            f"{LINKS} AND l.kind='class_day' AND l.section_id=? ORDER BY l.class_date, l.created_at, l.recording_id", (section_id,))]
        current = {row["recording_id"]: row["id"] for row in self.db.execute(
            f"{CURRENT} AND g.recording_id IN ({','.join('?' * len(rows))})", [row["recording_id"] for row in rows])} if rows else {}
        runs = {current[row["recording_id"]]: row for row in rows if row["recording_id"] in current}
        for row in rows:
            entry = days.setdefault(row["class_date"], {"recordings": [], "heard": [], "read": False, "numbers": [], "items": {}, "at": {}})
            entry["recordings"].append(row["recording_id"])
            entry["read"] = entry["read"] or row["recording_id"] in current
        said = [dict(row) for row in self.db.execute(
            f"""SELECT reading_id, item_id, start_ms, about_json FROM recording_item_readings
                WHERE kind='covered' AND reading_id IN ({','.join('?' * len(runs))}) ORDER BY start_ms, place""", list(runs))] if runs else []
        state = settled(self.db, [row["item_id"] for row in said])
        for row in said:
            if state.get(row["item_id"]) == "dismissed":
                continue
            entry, run = days[runs[row["reading_id"]]["class_date"]], runs[row["reading_id"]]
            if run["recording_id"] not in entry["heard"]:
                entry["heard"].append(run["recording_id"])
            for number in json.loads(row["about_json"] or "{}").get("notes") or []:
                if number not in entry["numbers"]:
                    entry["numbers"].append(number)
                    entry["at"][number] = (run["recording_id"], row["start_ms"])
                entry["items"].setdefault(number, []).append(row["item_id"])
        self._reached[section_id] = days
        return days

    def cards(self, section_id):
        """{class date: the action IDs of the move cards made for that section's day, the latest last}."""
        if section_id not in self._cards:
            found = {}
            for row in self.db.execute(
                    """SELECT json_extract(e.detail_json,'$.date') day, json_extract(e.detail_json,'$.action_id') action
                       FROM recording_item_events e WHERE e.event='carded' AND json_extract(e.detail_json,'$.card')='move'
                        AND json_extract(e.detail_json,'$.section_id')=? ORDER BY e.rowid""", (section_id,)):
                made = found.setdefault(row["day"], [])
                if row["action"] in made:
                    made.remove(row["action"])
                made.append(row["action"])
            self._cards[section_id] = found
        return self._cards[section_id]

    def declined(self, action_id):
        """Whether the instructor declined that card: their word that the plan stands."""
        if not action_id or not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assistant_action_events'").fetchone():
            return False
        last = self.db.execute("SELECT kind FROM assistant_action_events WHERE action_id=? ORDER BY id DESC LIMIT 1", (action_id,)).fetchone()
        return bool(last) and last[0] == "declined"


def term(db, term_id, memo=None, read=None):
    """The one `Term` of a read (`memo` lasts that read)."""
    if memo is None:
        return Term(db, term_id, read)
    key = ("class-term", term_id)
    if key not in memo:
        memo[key] = Term(db, term_id, read)
    return memo[key]


def compare(found, section_id, day):
    """COVERED AGAINST PLANNED for one section's class day, or None when no recording is filed on it:
    {recordings, carrier, state, planned, note, own, reached, not_reached, ahead, earlier, next_day, card_id, card_ids}.
    `carrier` is the ONE recording of the day that carries its card and its count: the first whose reading kept a
    section (two recordings of one class are one class day), else the first. `state`:
      unread      no reading of its recording yet;
      none        its reading kept no notes section, so nothing is set against the plan;
      unplanned   no topics were planned for the day: what was reached is shown, and nothing differs;
      as_planned  every planned section was reached, that day or on an earlier recorded class day;
      differs     a planned section was not reached (orange: the move is offered);
      left        it differs and the instructor declined the move: the plan stands."""
    here = found.reached(section_id).get(day)
    if not here:
        return None
    planned = found.topics(section_id, day)
    wanted = planned["notes"] if planned else []
    before = {}
    for other, entry in found.reached(section_id).items():
        if other < day:
            for number in entry["numbers"]:
                before[number] = min(before.get(number, other), other)
    missing = [number for number in wanted if number not in here["numbers"]]
    not_reached = [number for number in missing if number not in before]
    days, _ = found.meetings(section_id)
    cards = found.cards(section_id).get(day) or []
    card = cards[-1] if cards else None
    state = "unread" if not here["read"] else "none" if not here["numbers"] else "unplanned" if not wanted \
        else "differs" if not_reached else "as_planned"
    if state == "differs" and found.declined(card):
        state = "left"
    return {"recordings": list(here["recordings"]), "carrier": (here["heard"] or here["recordings"])[0], "card_ids": list(cards),
            "state": state, "planned": wanted, "note": planned["note"] if planned else "",
            "own": bool(planned and planned["own"]), "reached": list(here["numbers"]), "not_reached": not_reached,
            "ahead": [number for number in here["numbers"] if number not in wanted],
            "earlier": [{"number": number, "date": before[number]} for number in missing if number in before],
            "next_day": min((other for other in days if other > day), default=""), "card_id": card, "items": here["items"], "at": here["at"]}


def move(found, section_id, day, compared):
    """THE MOVE as a change to the term's class schedule (also for a day whose move the instructor declined: their own press
    may propose it again), or None when there is nothing to move or no later class day:
    {term_id, base_revision, topics, moved, to}. This section's own topics for the day lose what was not reached, and its own
    topics for its next class day begin with it; the term's entries and the other sections' are left as they are. A day left
    with nothing keeps a note saying where its topics went (an own entry that says nothing would fall back to the term's)."""
    if not compared or blocked(found, section_id, compared):
        return None
    moved, after = compared["not_reached"], compared["next_day"]

    def own(entry, notes, note):
        entry = entry or {"notes": [], "note": "", "sections": {}}
        return {"notes": list(entry["notes"]), "note": entry["note"], "sections": {**entry.get("sections", {}), section_id: {"notes": notes, "note": note}}}

    plan = found.plan
    kept = [number for number in compared["planned"] if number not in moved]
    later = found.topics(section_id, after) or {"notes": [], "note": ""}
    topics = {day: own(plan["topics"].get(day), kept, compared["note"] or ("" if kept else f"{listed(moved)} moved to {label(after)}")),
              after: own(plan["topics"].get(after), moved + [number for number in later["notes"] if number not in moved], later["note"])}
    return {"term_id": found.term_id, "base_revision": plan["revision"], "topics": topics, "moved": list(moved), "to": after}


def blocked(found, section_id, compared):
    """Why no move can be proposed for a day, in the page's words; '' when one can. Nothing to move says so plainly; a day
    with no later class day, and a next class day that would then hold more topics than a class day may
    (scheduling.class_schedule.MAX_NOTES: the class schedule's own save would refuse it), are the instructor's to settle."""
    from scheduling.class_schedule import MAX_NOTES
    if compared["state"] not in ("differs", "left"):
        return "Nothing planned for this day is unreached."
    if not compared["next_day"]:
        return "This section has no later class day to move it to."
    later = found.topics(section_id, compared["next_day"]) or {"notes": []}
    if len(compared["not_reached"]) + len([number for number in later["notes"] if number not in compared["not_reached"]]) > MAX_NOTES:
        return f"{label(compared['next_day'])} would then hold more than {MAX_NOTES} topics. Edit that day's topics on the class schedule."
    return ""


def view(found, section_id, day, recording_id):
    """One class recording's class day as its page shows it: the comparison with each notes section's title, one plain line
    saying how it stands, where to hear each section begin, and the move Faculty would propose (`move`, for the service)."""
    compared = compare(found, section_id, day)
    if compared is None:
        return None
    titles = found.notes

    def named(number):
        recording, at = compared["at"].get(number, ("", None))
        return {"number": number, "title": titles.get(number, ""),
                **({"start_ms": at} if at is not None and recording == recording_id else {})}

    state, reached, missing = compared["state"], compared["reached"], compared["not_reached"]
    if state == "unread":
        text = ""
    elif state == "none":
        text = "The reading found no notes section this class worked through, so nothing is set against the plan."
    elif state == "unplanned":
        text = f"Reached {listed(reached)}. No topics were planned for this day."
    elif state == "as_planned":
        text = "Reached what was planned for this day."
    else:
        text = f"Not reached: {listed(missing)}."
    return {"section_id": section_id, "section": found.number(section_id), "date": day, "day": label(day), "href": found.href(section_id),
            "state": state, "text": text, "planned": [named(number) for number in compared["planned"]], "note": compared["note"],
            "own": compared["own"], "reached": [named(number) for number in reached], "not_reached": [named(number) for number in missing],
            "ahead": [named(number) for number in compared["ahead"]],
            "earlier": [{**named(entry["number"]), "date": entry["date"], "day": label(entry["date"])} for entry in compared["earlier"]],
            "next_day": compared["next_day"], "next": label(compared["next_day"]) if compared["next_day"] else "",
            "recordings": compared["recordings"], "carrier": compared["carrier"], "card_id": compared["card_id"], "card_ids": compared["card_ids"],
            "items": compared["items"], "move": move(found, section_id, day, compared),
            "blocked": blocked(found, section_id, compared) if state in ("differs", "left") else ""}


def recorded(db, section_id, read):
    """What a section's class schedule shows of its recorded class days (SCHEDULING "CLASS SCHEDULE: WHAT A RECORDING REACHED"):
    {date: {recording_id, href, read, reached, not_reached, differs}}, by reference. `read` is the schedule's own memo of the
    term. {} for a catalog with no recordings."""
    if not tables(db):
        return {}
    found, days = Term(db, read.term_id, read), {}
    for day in found.reached(section_id):
        compared = compare(found, section_id, day)
        first = compared["carrier"]
        days[day] = {"recording_id": first, "href": f"#recordings/{first}", "read": compared["state"] != "unread",
                     "reached": [{"number": number, "title": found.notes.get(number, "")} for number in compared["reached"]],
                     "not_reached": compared["not_reached"] if compared["state"] in ("differs", "left") else [],
                     "differs": compared["state"] == "differs"}
    return days


def announced(found, section_id, item):
    """ANNOUNCED AGAINST THE RECORDS: what the section's class schedule holds for the day an `announced` item names, as
    {state, text, href} or None when Faculty holds nothing to set it against. `agrees`: a record has that day; `differs`:
    its records have another day (the text says both); `info`: the work is listed and has no date yet. The class schedule's
    read already joins the day a quiz or exam is given, the section's due date in Faculty's Canvas copy, the gradebook's
    and a planned row (SCHEDULING "CLASS SCHEDULE"), each named here as the record it came from."""
    about, said = item.get("about") or {}, item.get("date")
    if not said:
        return None
    href = found.href(section_id)
    if about.get("what") == "work":
        work = next((entry for entry in found.read.works if entry["key"] == about.get("work")), None)
        facts = found.read.facts(work, section_id) if work else None
        if not facts:
            return None
        title = work["title"] or (facts["row"] or {}).get("title") or "this work"
        held = [(VERB["due"] if what == "due" else VERB[facts["role"]], fact) for what, fact in (("given", facts["given"]), ("due", facts["due"])) if fact and fact.get("date")]
        if not held:
            return {"state": "info", "text": f"Faculty holds no date for {title} yet.", "href": href}
        same = next(((verb, fact) for verb, fact in held if fact["date"] == said), None)
        if same:
            return {"state": "agrees", "href": href, "text": f"As Faculty has it: {title} is {same[0]} {label(said)} ({SOURCE.get(same[1]['source'], 'its record')})."}
        both = " and ".join(f"{verb} {label(fact['date'])} ({SOURCE.get(fact['source'], 'its record')})" for verb, fact in held)
        return {"state": "differs", "href": href, "said": said, "held": [{"what": verb, "date": fact["date"], "source": fact["source"]} for verb, fact in held],
                "text": f"You told the class {label(said)}. Faculty has {title} {both}."}
    if about.get("what") == "class":
        days, off = found.meetings(section_id)
        meets = days.get(said) or []
        clock = display_clock(meets[0]["time"]) if meets and meets[0].get("time") else ""
        if item.get("happening"):
            if meets:
                return {"state": "agrees", "href": href, "text": f"As Faculty has it: Section {found.number(section_id)} meets {label(said)}{' at ' + clock if clock else ''}."}
            return {"state": "differs", "href": href, "said": said, "held": [],
                    "text": f"You told the class it meets {label(said)}. The class schedule has no class for Section {found.number(section_id)} that day"
                            f"{' (a day off)' if said in off else ''}."}
        if meets:
            return {"state": "differs", "href": href, "said": said, "held": [{"what": "class", "date": said, "source": "schedule"}],
                    "text": f"You told the class there is no class {label(said)}. The class schedule still has Section {found.number(section_id)} meeting that day"
                            f"{' at ' + clock if clock else ''}."}
        return {"state": "agrees", "href": href, "text": f"As Faculty has it: no class for Section {found.number(section_id)} on {label(said)}."}
    return None
