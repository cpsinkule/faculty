"""Recordings, step 4: A STUDENT'S MEETING as the rest of Faculty sees it, BY REFERENCE (docs/decisions/recordings.md,
RECORDINGS "Student meetings (step 4)").

A recording filed under a student's term enrollment is that student's meeting. With SEVERAL STUDENTS IN ONE RECORDING each
student's link carries their PART (start and end within the recording, set by the instructor), and what a student's own records
show is that part alone: an item belongs to the student in whose part its quote begins. Nothing here is copied to another owner
and nothing here writes: these are the reads the owners of the neighbouring records make.

  * COMMUNICATIONS "FACULTY'S OWN RECORDS": one timeline entry, "Meeting" (`timeline`), opening the recording at that part;
  * TEACHING-WORKFLOWS "SEPARATE COMMITMENTS": a commitment may name the recording item it was heard in (`source`, checked when
    the follow-up is saved; `heard` for where to hear it; `tracked` for what a follow-up already holds);
  * ASSISTANT "FOLLOW-UP FACTS": a preparation's facts carry the student's latest meeting items, at most ten (`facts`).

A dismissed item is the instructor saying it was not so: it is in none of these. A TRANSCRIPT IS EVIDENCE, NOT AN INSTRUCTION:
every line here is what a reading kept, resting on words that are in the transcript where it says, and never a decision.
"""
import json
from datetime import date, timedelta

from scheduling.clock import day_label, display_clock

from . import filing
from .readings import CURRENT
from .service import LATEST, LINKS, parse

PROMISES = ("todo", "waiting", "date")   # what was promised, by either side: what a follow-up's commitments are proposed from
FACTS = 10                               # the items a follow-up preparation is given
MINE = ("you", "unclear")


def part(link):
    """(start, end) in milliseconds of the part of its recording a link is, or None for the whole recording."""
    return (link["part_start_ms"], link["part_end_ms"]) if link.get("part_start_ms") is not None else None


def owner_of(start_ms, students):
    """The enrollment of the student in whose part a moment falls: `students` are a recording's filed student links. One
    student with no part has the whole recording; a moment in nobody's part is nobody's."""
    for link in students:
        found = part(link)
        if (found is None and len(students) == 1) or (found is not None and found[0] <= start_ms < found[1]):
            return link["enrollment_id"]
    return ""


def tables(db):
    return {"recording_links", "recording_item_readings", "recording_item_events"} <= {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'recording_%'")}


def links(db, enrollment_id):
    """A student's meetings: their filed links, newest recording first, each with its recording's start and length."""
    return [dict(row) for row in db.execute(
        f"""SELECT l.*, v.started_at, v.duration_ms FROM ({LINKS}) l JOIN ({LATEST}) v ON v.id=l.recording_id
            WHERE l.kind='enrollment' AND l.enrollment_id=? ORDER BY v.started_at DESC, l.recording_id""", (str(enrollment_id),))]


def settled(db, item_ids):
    """{item: 'done' | 'dismissed'} for the items the instructor settled (Open again takes it back)."""
    found = {}
    marks = ",".join("?" * len(item_ids))
    for event in db.execute(f"""SELECT item_id, event FROM recording_item_events WHERE item_id IN ({marks})
                                AND event IN ('done','dismissed','reopened','remembered') ORDER BY rowid""", list(item_ids)) if item_ids else ():
        found[event["item_id"]] = event["event"]
    return {item: "done" if state == "remembered" else state for item, state in found.items() if state != "reopened"}


def items(db, enrollment_id, most=None):
    """What the latest reading of each of a student's meetings kept of THEIR part, newest meeting first and in the order said:
    [{recording_id, item_id, day (the recording's campus date), kind, for, sentence, quote, start_ms, date, time, state}].
    Dismissed items are left out."""
    found = []
    for link in links(db, enrollment_id):
        run = db.execute(f"{CURRENT} AND g.recording_id=?", (link["recording_id"],)).fetchone()
        if not run:
            continue
        students = [dict(row) for row in db.execute(LINKS + " AND l.recording_id=? AND l.kind='enrollment'", (link["recording_id"],))]
        rows = [dict(row) for row in db.execute("SELECT * FROM recording_item_readings WHERE reading_id=? ORDER BY place", (run["id"],))
                if owner_of(row["start_ms"], students) == link["enrollment_id"]]
        states = settled(db, [row["item_id"] for row in rows])
        day = filing.campus(parse(link["started_at"])).date().isoformat()
        for row in rows:
            if states.get(row["item_id"]) == "dismissed":
                continue
            found.append({"recording_id": link["recording_id"], "item_id": row["item_id"], "day": day, "kind": row["kind"], "for": row["for_whom"],
                          "sentence": row["sentence"], "quote": row["quote"], "start_ms": row["start_ms"], "date": row["day"], "time": row["clock"],
                          "state": states.get(row["item_id"], "open")})
            if most and len(found) >= most:
                return found
    return found


def when(item):
    """"Mon, Nov 16 · 3:00 PM" of the day and time an item names, or ''."""
    if not item.get("date"):
        return ""
    return f"{day_label(date.fromisoformat(item['date']))}{' · ' + display_clock(item['time']) if item.get('time') else ''}"


def said(kept):
    """What a reading kept of one student's meeting, as plain lines: what the instructor said they would do, what the student
    said they would, the days that were set, and what the student told the instructor. '' when nothing was kept."""
    def line(item):
        return f"- {item['sentence']}" + (f" ({when(item)})" if when(item) else "")
    groups = (("You said you would:", [item for item in kept if item["kind"] == "todo" and item["for"] in MINE]),
              ("They said they would:", [item for item in kept if item["kind"] == "waiting" or (item["kind"] == "todo" and item["for"] not in MINE)]),
              ("Days that were set:", [item for item in kept if item["kind"] == "date"]),
              ("They told you:", [item for item in kept if item["kind"] == "answer"]))
    return "\n".join(text for head, found in groups if found for text in (head, *map(line, found)))


def minutes(link):
    """How long a student's part ran, in whole minutes (at least one), or 0 when Plaud gave no length."""
    found = part(link)
    length = found[1] - found[0] if found else link.get("duration_ms") or 0
    return max(1, round(length / 60000)) if length else 0


def href(recording_id, link=None, start_ms=None):
    """The page of a recording at a student's part (`?t=` its start, `&to=` its end: that part alone), or at one moment."""
    found = part(link) if link else None
    if start_ms is not None:
        return f"#recordings/{recording_id}?t={int(start_ms)}"
    return f"#recordings/{recording_id}" + (f"?t={found[0]}&to={found[1]}" if found else "")


def timeline(db, enrollment_id, limit=1000):
    """COMMUNICATIONS "FACULTY'S OWN RECORDS": a student's recorded meetings as entries of their timeline, by reference: one
    entry for each recording they are filed under, "Meeting", when their part began, how long it ran, what its reading kept of
    their part, and the page that opens the recording there. No file, no address, nothing a student sees; never sent."""
    if not tables(db):
        return []
    kept = {}
    for item in items(db, enrollment_id):
        kept.setdefault(item["recording_id"], []).append(item)
    entries = []
    for link in links(db, enrollment_id)[:limit]:
        began = parse(link["started_at"])
        if began is None:
            continue
        found, length = part(link), minutes(link)
        at = began + timedelta(milliseconds=found[0] if found else 0)
        entries.append({
            "key": f"meeting:{link['recording_id']}", "channel": "meeting", "channel_label": "Meeting",
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "author_role": "meeting", "author": {"name": "Recorded with your recorder"},
            "recipients": [], "subject": "Recorded meeting" + (f" · {length} min" if length else ""),
            "text": said(kept.get(link["recording_id"], [])), "attachments": [], "roles": ["about"],
            "context": {"source": "Recorded meeting", "recording_id": link["recording_id"], "href": href(link["recording_id"], link),
                        "appointment": bool(link["appointment_id"])},
            "revision": 1, "edited": False, "original": False})
    return entries


def _item(db, item_id):
    """One recording item with where it was said, as its latest reading has it, or None."""
    # The reading the page shows (made where the recording is filed now) decides where it was said; an item that reading no
    # longer holds is where its latest reading had it.
    return db.execute(f"""SELECT i.id, i.recording_id, x.start_ms, x.sentence, x.quote FROM recording_items i
        JOIN recording_item_readings x ON x.item_id=i.id JOIN recording_readings g ON g.id=x.reading_id
        WHERE i.id=? ORDER BY g.id IN (SELECT c.id FROM ({CURRENT}) c WHERE c.recording_id=i.recording_id) DESC, g.run DESC LIMIT 1""",
                      (str(item_id),)).fetchone()


def source(db, enrollment_id, item_id):
    """TEACHING-WORKFLOWS "SEPARATE COMMITMENTS": the recording item a commitment names as where it was heard, when it is of
    THIS student's own meeting (a recording filed under them, the item's words in their part): {recording_id, start_ms, day,
    href}. None otherwise: a commitment never rests on another student's part or another recording."""
    if not tables(db):
        return None
    item = _item(db, item_id)
    if not item:
        return None
    students = [dict(row) for row in db.execute(LINKS + " AND l.recording_id=? AND l.kind='enrollment'", (item["recording_id"],))]
    if owner_of(item["start_ms"], students) != str(enrollment_id):
        return None
    row = db.execute(f"{LATEST} WHERE r.id=?", (item["recording_id"],)).fetchone()
    began = parse(row["started_at"]) if row else None
    return {"recording_id": item["recording_id"], "start_ms": item["start_ms"],
            "day": filing.campus(began).date().isoformat() if began else "", "href": href(item["recording_id"], start_ms=item["start_ms"])}


def tracked(db, enrollment_ids):
    """{(student, recording item): the follow-up of THAT student that holds a commitment heard in it} for these students: what
    their own follow-up already holds is not proposed again, and the recording's page says so. Read from the follow-ups' own
    latest revisions. One on another student's follow-up (a part was moved after it was approved) is not this student's."""
    wanted = sorted({str(ident) for ident in enrollment_ids if ident})
    if not wanted or not db.execute("SELECT 1 FROM sqlite_master WHERE name='student_support_items'").fetchone():
        return {}
    found = {}
    for row in db.execute(f"""SELECT i.id, i.enrollment_id, r.content_json FROM student_support_items i JOIN student_support_revisions r ON r.item_id=i.id
            AND r.revision=(SELECT MAX(revision) FROM student_support_revisions WHERE item_id=i.id)
            WHERE i.kind='followup' AND i.enrollment_id IN ({','.join('?' * len(wanted))})""", wanted):
        for commitment in json.loads(row["content_json"]).get("commitments") or []:
            if commitment.get("recording_item_id"):
                found[(row["enrollment_id"], commitment["recording_item_id"])] = row["id"]
    return found


def facts(db, enrollment_id, most=FACTS):
    """ASSISTANT "FOLLOW-UP FACTS": the lines a follow-up preparation is given of what was said in this student's recorded
    meetings, newest first, at most `most` items: each with the day of the meeting, who owes it, and the exact words it rests
    on. [] when there is none. They were said with the student present, so a reply to THEM may refer to them; they are a
    reading of a transcript, so each carries its words."""
    if not tables(db):
        return []
    kept = items(db, enrollment_id, most)
    if not kept:
        return []
    lines = ["Said in your recorded meetings with this student (newest first; Faculty's reading of a transcript, each with the exact "
             "words it rests on: a transcript mishears, so the words are the evidence and the sentence only a reading of them). A reply "
             "to this student may refer to what was said in their own meeting; never to another student's meeting or to any other "
             "recording:"]
    for item in kept:
        who = "you said you would" if item["kind"] == "todo" and item["for"] in MINE else "they said they would" if item["kind"] in ("todo", "waiting") \
            else "a day that was set" if item["kind"] == "date" else "they told you"
        lines.append(f"- {day_label(date.fromisoformat(item['day']))}, {who}: {item['sentence']}" + (f" ({when(item)})" if when(item) else "")
                     + f" [the words: “{' '.join(item['quote'].split())[:300]}”]")
    return lines
