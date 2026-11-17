"""Recordings, steps 3 and 4: THE READING, and what a meeting left the instructor with (docs/decisions/recordings.md, RECORDINGS
"The reading (step 3)", "What an item may become", "Student meetings (step 4)").

ON ITS OWN, WITHOUT A CARD. A recording filed under a term or a course (a coordinator or instructor meeting), under a
student (step 4: by its own instructions, for what was promised) or on a section's class day (step 5: by its own instructions,
for the notes sections the class worked through and the days the instructor told it; a memo is read from step 6), whose kept
transcript is read into lines is READ by the Assistant's own
engine, the connection Compute settings name: ONE request holding the transcript's lines and the FACTS, never the audio and
never Plaud's summary. What comes back passes THE QUOTE CHECK (recordings/reading.py: code) and is kept as an immutable RUN
with its items (recordings/migrations/003_readings.sql). A capture round reads at most READINGS recordings, after it has said
what it says of Plaud, in the capture thread and under the capture lock; Read again asks the worker for one.

  FACTS, each read through its owning service and never copied: the recording's day; which speaker is the instructor (the
  workspace's own name against the speakers' names); the term's coordinator; its sections and their class days; the calendar
  three weeks either side (Faculty's own entries and the Google entries of this course: no student's appointment, makeup or
  testing center booking, and no other Google entry); the term's assessments and their given dates; the names of the
  modules, pages and assignments in Faculty's copy of the Canvas course.
  A RUN that the engine could not make is no run: why is said on the page and it is tried again later, less often, and after
  five tries only on Read again. An answer Faculty cannot use is a `failed` run.
  AN ITEM keeps its identity by its kind and quote, so what the instructor answered stays answered when the recording is read
  again. What became of it is an event: done, dismissed, reopened; remembered; carded; accepted or declined (a ruling).
  CHECKED AGAINST FACULTY (recordings/checks.py: code, never a model) at each read of the page; nothing of it is saved.

WHAT AN ITEM MAY BECOME, never by itself:
  an APPROVE CARD for a calendar entry, where something said to happen on a day is not on the calendar: made after the reading
  through the Assistant's own actions and the Calendar's own route, as every background proposal is (it waits for Approve);
  a MEMORY, only by the instructor's own press of Remember on a `policy` item, in the words shown, which they may edit first
  (source `recording`; the roster check and the decision host's check apply as to any memory they write);
  a RULING waits on its assessment: the rubric's finalization lists it with its quote, and the instructor accepts or declines
  it there; never on a certified plan;
  from A STUDENT'S MEETING (step 4), ONE Approve card proposing what was promised as commitments on that student's follow-up
  (opening one where none is open) and ONE proposing a private note, each through its owning module's own route; with
  several students in one recording, an item is the student's in whose part its quote begins (recordings/meetings.py);
  from A CLASS (step 5), ONE Approve card proposing that what the class did not reach of the day's planned topics moves to the
  section's next class day, through the class schedule's own route (recordings/classes.py). A class raises nothing else:
  no calendar entry, no memory, no ruling, and never anything about one student.

  from A MEMO (step 6: the instructor dictating alone, read by its own instructions), each REQUEST is put to the Assistant
  as a background preparation, once, when Faculty is sure the words are the instructor's (recordings/memos.py `voice`),
  and otherwise only by their own press; every change that run wants waits as an Approve card. A rule dictated in a memo
  is kept only by Remember, as any other.
A TRAINING (step 6: filed under a course for reference) is kept, shown and searched, and is NOT read.

Lock order: the capture lock (a round), reads, the engine's request with no lock held, then the roster lifecycle lock and ONE
catalog transaction. The Assistant's own locks are taken only after that transaction has committed.
"""
import json
import logging
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import encoded
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect
from scheduling.clock import day_label, display_clock

from . import checks, filing, memos, reading, transcript
from .service import LATEST, LINKS, iso, parse

LOG = logging.getLogger(__name__)
READ_KINDS = ("term", "course", "enrollment", "class_day", "memo")   # what is read: coordinator and instructor meetings (step 3), a student's (step 4), a class (step 5), a memo (step 6)
READ_SQL = "('term','course','enrollment','class_day','memo')"   # ... and never a TRAINING (a course link whose purpose says so)
READINGS = 2                             # recordings read in one round (each is one request of a minute or more)
WAIT = 900                               # seconds one request may take on a connection that takes a limit (never a hosted model's)
AGAIN = (600, 3600, 6 * 3600, 24 * 3600)   # a reading that could not be made is tried again after this long, then daily,
TRIES = len(AGAIN) + 1                   # ... and after this many tries only on Read again
OFF_AGAIN = 3600                         # while the Assistant is off, looked at again this often (nothing is asked)
EITHER_SIDE = timedelta(days=21)         # the calendar a reading is told
ENTRIES = 150                            # ... and the most entries of it
SETTLES = ("done", "dismissed", "reopened")
STUDENT_CARDS = ("commitments", "note")  # the two cards of a student's meeting (step 4), each ONE card for several items
MOVE = "move"                            # the one card of a class (step 5): what was not reached moves to the next class day
MOVES_SINCE = timedelta(days=2)          # a move card waits a day at most: only cards made this lately are looked at again
CLASS_BEFORE, CLASS_AFTER = timedelta(days=21), timedelta(days=42)   # the section's work a class reading is told (titles only)
NOTES, WORK = 400, 60                    # ... and the most notes sections and pieces of work among its facts
RULED = ("accepted", "declined")
OFF = "The Assistant is off (Compute settings name no model for it), so recordings are not read."
NOT_READ = "Faculty reads meetings filed under a term, a course or a student, classes and memos."
TRAINING = ("A training is kept for reference under its course and is not read for items. Its words are found by the search of what was said. "
            "File it under the course to have it read as a meeting.")
FILED = f"""(SELECT group_concat(owner_key, '|') FROM (SELECT l.owner_key FROM recording_links l WHERE l.state='filed'
      AND l.revision=(SELECT MAX(revision) FROM recording_links WHERE recording_id=l.recording_id AND owner_key=l.owner_key)
      AND l.recording_id=v.id AND l.kind IN {READ_SQL} AND l.purpose='' ORDER BY l.owner_key))"""
READABLE = f"""SELECT v.id, v.started_at, COALESCE(s.requested_at,'') requested_at FROM ({LATEST}) v
    JOIN recording_transcripts t ON t.file_id=v.transcript_file_id AND t.readable=1 AND t.lines>0
    LEFT JOIN recording_reading_state s ON s.recording_id=v.id
    WHERE EXISTS ({LINKS} AND l.recording_id=v.id AND l.kind IN {READ_SQL} AND l.purpose='')"""
DUE = f"""{READABLE} AND (COALESCE(s.requested_at,'')!='' OR (COALESCE(s.next_try_at,'')<=? AND COALESCE(s.tries,0)<? AND NOT EXISTS (
        SELECT 1 FROM recording_readings g WHERE g.recording_id=v.id AND g.transcript_file_id=v.transcript_file_id
         AND g.status='read' AND g.filed_as={FILED})))
    ORDER BY COALESCE(s.requested_at,'')='', v.started_at DESC LIMIT ?"""
# The reading the page shows: a recording's latest `read` run made where the recording is filed NOW (one filed elsewhere and
# then back is shown the reading it had there).
CURRENT = f"""SELECT g.* FROM recording_readings g JOIN ({LATEST}) v ON v.id=g.recording_id
    WHERE g.status='read' AND g.filed_as={FILED}
      AND g.run=(SELECT MAX(run) FROM recording_readings WHERE recording_id=g.recording_id AND status='read' AND filed_as=g.filed_as)"""


class Waits(Exception):
    """A reading that could not be made now. `counted` is False where nothing was asked (the Assistant is off)."""

    def __init__(self, detail, counted=True):
        super().__init__(detail)
        self.detail, self.counted = detail[:2000], counted


def filed_as(links):
    """What a recording is filed under, as a reading remembers it: one filed elsewhere is read again."""
    return "|".join(sorted(link["owner_key"] for link in links if link["kind"] in READ_KINDS and not link.get("purpose")))


def _words(name):
    return {word for word in "".join(c if c.isalnum() else " " for c in str(name or "").casefold()).split() if len(word) > 1}


def when(item):
    """"Mon, Nov 16 · 3:00 PM" of an item's day and time, or the words that were said for it."""
    if not item["date"]:
        return item["date_words"]
    return f"{day_label(date.fromisoformat(item['date']))}{' · ' + display_clock(item['time']) if item['time'] else ''}"


class Readings:
    def __init__(self, record):
        self.record, self.path = record, record.path
        self.frq = None         # the grading service, for the Assistant's engine as Compute settings name it (set by create_app)
        self.assistant = None   # the Assistant's service: Approve cards and memories (set by create_app)
        self.support = None     # the follow-up service: a student's open follow-up, as its own listing answers it (set by create_app)
        # Remember and Propose check, then act through the Assistant's own service, then record: one press at a time here, so two
        # overlapping presses never keep two memories or raise two cards. (The worker's own proposal after a reading is another
        # process; it and a press for the same item in the same instant could both make a card, each still waiting for Approve.)
        self._pressing = threading.Lock()

    # -- When ----------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _state(db, recording_id, **values):
        values["updated_at"] = timestamp()
        db.execute("INSERT OR IGNORE INTO recording_reading_state(recording_id) VALUES (?)", (recording_id,))
        db.execute(f"UPDATE recording_reading_state SET {', '.join(key + '=?' for key in values)} WHERE recording_id=?",
                   (*values.values(), recording_id))

    def wanted(self):
        """Whether the instructor asked for a reading (Read again) that no round has made yet."""
        with connect(self.path) as db:
            return bool(db.execute("SELECT 1 FROM recording_reading_state WHERE requested_at!='' LIMIT 1").fetchone())

    def read_due(self):
        """Read what is due, at most READINGS recordings: one asked for first, then the newest. Called by a round, under the
        capture lock. Returns how many readings were made."""
        with connect(self.path) as db:
            due = [row["id"] for row in db.execute(DUE, (iso(self.record.now()), TRIES, READINGS))]
            if len(due) < READINGS:
                # Nothing else can be read now: a request for a recording that is no longer one Faculty reads is dropped.
                marks = ",".join("?" * len(due))
                db.execute(f"UPDATE recording_reading_state SET requested_at='', request_id='', actor='' WHERE requested_at!=''"
                           f"{' AND recording_id NOT IN (' + marks + ')' if due else ''}", due)
        made = 0
        for recording_id in due:
            try:
                made += self._read(recording_id) == "read"
            except Exception as error:  # noqa: BLE001 - one recording's reading never stops the others or the round
                LOG.exception("A recording could not be read")
                self._broke(recording_id, error)
        return made

    def _broke(self, recording_id, error):
        """A reading that failed in Faculty itself (not the engine): said on the page and tried again less often, like any
        other, so a recording that cannot be read is never asked about every round."""
        try:
            with connect(self.path) as db:
                state = db.execute("SELECT tries, requested_at FROM recording_reading_state WHERE recording_id=?", (recording_id,)).fetchone()
            self._later(recording_id, state["tries"] if state else 0, Waits(f"Faculty could not keep this reading ({type(error).__name__})."),
                        state["requested_at"] if state else "")
        except Exception:  # noqa: BLE001 - the catalog itself is unwell: the next round says so
            LOG.warning("A failed reading's next try could not be recorded", exc_info=True)

    def ask(self, recording_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/read` (Read again): marks a reading wanted; the worker makes it within seconds as a new
        run. A repeated request ID asks nothing again."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self.record._one(db, recording_id)
                if not db.execute(f"{READABLE} AND v.id=?", (row["id"],)).fetchone():
                    raise Conflict("Faculty has no transcript of this recording to read, or it is not filed under a term, a course, a student, a class day or as a memo.")
                seen = db.execute("SELECT 1 FROM recording_reading_state WHERE request_id=? UNION ALL SELECT 1 FROM recording_readings WHERE request_id=?",
                                  (request_id, request_id)).fetchone()
                if not seen:
                    self._state(db, row["id"], requested_at=iso(self.record.now()), request_id=request_id, actor=str(actor)[:100],
                                tries=0, next_try_at="", detail="")
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.record.recording(recording_id)

    # -- What a reading is told ----------------------------------------------------------------------------------------------
    def _lines(self, db, row):
        _, speakers = self.record._speakers(db, row)
        shown = {speaker["key"]: speaker["shown"] for speaker in speakers}
        return [{"line": line["position"], "start_ms": line["start_ms"], "end_ms": line["end_ms"], "speaker_key": line["speaker_key"],
                 "speaker": shown.get(line["speaker_key"], line["speaker"] or "Unnamed speaker"), "text": line["text"]}
                for line in db.execute("SELECT * FROM recording_lines WHERE file_id=? ORDER BY position", (row["transcript_file_id"],))]

    @staticmethod
    def on_course_card(db, term_id):
        """COORDINATOR "ONE NAME": the coordinator the term's course card names (its latest answer, when the course is
        coordinated), or "". The course card's is the term's coordinator wherever Faculty names one."""
        from course_coordinators import service as coordinators
        card = coordinators.view(db, term_id) if term_id else None
        return card["name"] if card and card["coordinated"] else ""

    @classmethod
    def coordinator(cls, db, term_id):
        """The term's coordinator, as a reading is told and the page shows: the one on the course card; where the card names
        none, the name alone that was kept here for the term (`recording_coordinators`)."""
        named = cls.on_course_card(db, term_id)
        if named:
            return named
        row = db.execute("SELECT name FROM recording_coordinators WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone() if term_id else None
        return row["name"] if row else ""

    def _where(self, db, row, links):
        """Where a recording is filed, as a reading, the checks and the page need it: its term (the one it is filed under; a
        course's only current term), the course, the term's assessments, and the title of the entry it was filed by."""
        from seating import term_label
        # A STUDENT'S MEETING (step 4): filed under one student, or several each with their part, in the order they came.
        students = sorted((link for link in links if link["kind"] == "enrollment"), key=lambda link: (link["part_start_ms"] or 0, link["created_at"]))
        # A CLASS (step 5): filed on one section's class day; it has no student and is read by its own instructions.
        lesson = next((link for link in links if link["kind"] == "class_day"), None)
        # Step 6: A TRAINING is a course link whose purpose says so (kept for reference, never read); A MEMO has no owner.
        training = next((link for link in links if link["kind"] == "course" and link.get("purpose") == "training"), None)
        memo = next((link for link in links if link["kind"] == "memo"), None)
        link = next((link for link in links if link["kind"] == "term"), None) \
            or next((link for link in links if link["kind"] == "course" and not link.get("purpose")), None) \
            or (students[0] if students else None) or lesson
        columns = "SELECT id, code, term, term_other, year, definition_id FROM course_terms"
        terms = [dict(term) for term in db.execute(f"{columns} WHERE is_current=1 AND deleted_at IS NULL")]
        term = None
        if link and link["kind"] in ("term", "enrollment", "class_day"):
            found = db.execute(f"{columns} WHERE id=?", (link["term_id"],)).fetchone()
            term = dict(found) if found else None
        elif link:
            mine = [term for term in terms if term["definition_id"] == link["definition_id"]]
            term = mine[0] if len(mine) == 1 else None
        definition = term["definition_id"] if term else link["definition_id"] if link else training["definition_id"] if training else None
        meeting = ("memo" if memo else "training" if training else "") if link is None \
            else "student" if link["kind"] == "enrollment" else "class" if link["kind"] == "class_day" else "staff"
        code = term["code"] if term else ""
        if definition and not code:
            found = db.execute("SELECT code FROM course_definitions WHERE id=?", (definition,)).fetchone()
            code = found["code"] if found else ""
        assessments = self._assessments(db, term["id"], definition) if term else {}
        entries = (json.loads(link["basis_json"] or "{}").get("entries") or [{}]) if link else [{}]
        local = filing.campus(parse(row["started_at"]))
        return {"filed": filed_as(links), "term_id": term["id"] if term else None, "definition_id": definition, "code": code,
                "term_label": term_label(term) if term else "", "terms": [{key: term[key] for key in ("id", "code", "definition_id")} for term in terms],
                "assessments": assessments, "basis_title": str(entries[0].get("title") or ""), "recorded": local.date(),
                "began": local.strftime("%H:%M"), "meeting": meeting, "students": students if meeting == "student" else [],
                "class_day": {"section_id": link["section_id"], "date": link["class_date"]} if meeting == "class" else None}

    @staticmethod
    def _assessments(db, term_id, definition_id):
        """A term's assessments as a reading is told them and a name is matched to them, {the course's assignment id: title}:
        the ones the term has given a date (its `exam_events`, under the title the term gave), then the course's other
        assignments, as the Assignments page lists every one under the term whether or not it has a date yet (second live
        readings, 2026-11-13: the final exam was the course's "Final" with no date yet, so nothing said of it could name it).
        Read by reference: nothing is copied to the term."""
        found = {}
        try:
            for row in db.execute("SELECT assignment_id, title FROM exam_events WHERE term_id=? ORDER BY title, id", (term_id,)):
                found.setdefault(row["assignment_id"], row["title"])
            for row in db.execute("SELECT id, title FROM assignments WHERE definition_id=? ORDER BY title, id", (definition_id,)):
                found.setdefault(row["id"], row["title"])
        except sqlite3.Error:
            pass
        return found

    @staticmethod
    def _you(db, shown):
        """(the instructor's name, the key of the speaker Faculty takes as the instructor or ''): the one speaker whose name
        shares a word with the workspace's own name. No name, no such speaker or more than one: Faculty cannot tell. `shown` is
        {a speaker's key: the name they go by}."""
        try:
            row = db.execute("SELECT teacher_name FROM settings WHERE id=1").fetchone()
        except sqlite3.Error:
            row = None
        name = " ".join(str(row["teacher_name"] if row else "").split())
        mine = [key for key, called in shown.items() if _words(name) & _words(called)]
        return name, mine[0] if len(mine) == 1 else ""

    def _voice(self, db, recording_id):
        """(whether Faculty is sure a memo's words are the instructor's, why it is not): recordings/memos.py `voice`, from the
        voices of the recording's latest transcript as they are named now. Code, at each ask; nothing of it is saved."""
        shown = {speaker["key"]: speaker["shown"] for speaker in self.record._speakers(db, self.record._one(db, recording_id))[1]}
        return memos.voice(shown, self._you(db, shown)[0])

    def _assistant_on(self):
        """Whether a dictated request can be run now: the Assistant is here and Compute settings name a model for it."""
        if self.assistant is None or self.frq is None:
            return False
        try:
            return self.connection() is not None
        except ValueError:
            return False

    def _facts(self, db, row, lines, where):
        """(the facts a reading is told, {the short name an assessment goes by there: its ID}). Every fact is read here, from the
        table or service that owns it."""
        shown = {line["speaker_key"]: line["speaker"] for line in lines}
        name, you = self._you(db, shown)
        recorded, term_id = where["recorded"], where["term_id"]
        sections, given, canvas = [], {}, {}
        if term_id:
            from exam_workflow.given_dates import term_given
            from scheduling.class_schedule import section_meetings
            low, high = (recorded - EITHER_SIDE).isoformat(), (recorded + EITHER_SIDE).isoformat()
            for section in db.execute("SELECT id, number FROM course_sections WHERE term_id=? ORDER BY number", (term_id,)).fetchall():
                try:
                    days, _ = section_meetings(db, term_id, section["id"])
                except (sqlite3.Error, KeyError, ValueError):
                    days = {}
                meets = sorted({(date.fromisoformat(day).weekday(), meeting["time"]) for day, meetings in days.items() if low <= day <= high
                                for meeting in meetings})
                sections.append({"number": section["number"],
                                 "class_days": ", ".join(f"{('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[weekday]} {clock}".strip() for weekday, clock in meets)})
            try:
                given = {assignment: min((value["date"] for value in by_section.values() if value["date"]), default="")
                         for assignment, by_section in term_given(db, term_id).items()}
            except sqlite3.Error:
                given = {}
            try:
                canvas = checks.canvas_names(db, term_id)
            except (sqlite3.Error, ValueError):
                canvas = {}
        handles = {f"a{number}": assignment for number, assignment in enumerate(where["assessments"], 1)}
        facts = {"recorded": {"date": recorded.isoformat(), "weekday": f"{recorded:%A}", "began": where["began"]},
                 "instructor": {"name": name, "speaker": shown.get(you, "")},
                 "coordinator": self.coordinator(db, term_id),
                 "speakers": list(dict.fromkeys(shown.values())),
                 "course": where["code"], "term": where["term_label"], "sections": sections, "canvas": canvas,
                 "assessments": [{"id": handle, "title": where["assessments"][assignment], "given": given.get(assignment, "")}
                                 for handle, assignment in handles.items()]}
        return facts, handles

    def _class_facts(self, db, lines, where):
        """(what the reading of A CLASS (step 5) is told, {the short name a piece of work goes by there: its key on the class
        schedule}), each read through its owner: the recording's day, which speaker is the instructor, the course and the
        section, the course's notes sections by number and title (`course_notes.service.current_sections`), and the TITLES
        of the section's work around that day as its class schedule lists it. Never a date Faculty holds (the reading says
        what was SAID; the comparison is code's), never the day's planned topics, and nothing of any student."""
        shown = {line["speaker_key"]: line["speaker"] for line in lines}
        name, you = self._you(db, shown)
        recorded, section = where["recorded"], where["class_day"]["section_id"]
        notes, work, handles = [], [], {}
        try:
            from course_notes.service import current_sections
            notes = [{"number": number, "title": entry.get("title") or ""} for number, entry in current_sections(db, where["definition_id"]).items()][:NOTES]
        except sqlite3.Error:
            notes = []
        try:
            from scheduling.class_schedule import section_schedule
            view = section_schedule(db, section, (recorded - CLASS_BEFORE).isoformat(), (recorded + CLASS_AFTER).isoformat())
            for entry in [entry for day in view["days"] for entry in day["work"]] + view["undated"]:
                if entry["key"] not in handles.values() and entry["title"] and len(work) < WORK:
                    handles[f"w{len(work) + 1}"] = entry["key"]
                    work.append({"id": f"w{len(work) + 1}", "title": entry["title"][:160]})
        except (sqlite3.Error, KeyError, ValueError, NotFound):
            work, handles = [], {}
        number = db.execute("SELECT number FROM course_sections WHERE id=?", (section,)).fetchone()
        facts = {"recorded": {"date": recorded.isoformat(), "weekday": f"{recorded:%A}", "began": where["began"]},
                 "instructor": {"name": name, "speaker": shown.get(you, "")}, "speakers": list(dict.fromkeys(shown.values())),
                 "course": where["code"], "term": where["term_label"], "section": number["number"] if number else "",
                 "notes": notes, "work": work}
        return facts, handles

    def _memo_facts(self, db, lines, where):
        """What the reading of A MEMO (step 6) is told: the recording's day, which speaker is the instructor, the voices Plaud
        heard, and the course codes of the current terms (so a course said by its number is a course). Nothing of any
        student, and no record: the reading says what was ASKED, and what a request means is the Assistant's to look up."""
        shown = {line["speaker_key"]: line["speaker"] for line in lines}
        name, you = self._you(db, shown)
        recorded = where["recorded"]
        return {"recorded": {"date": recorded.isoformat(), "weekday": f"{recorded:%A}", "began": where["began"]},
                "instructor": {"name": name, "speaker": shown.get(you, "")}, "speakers": list(dict.fromkeys(shown.values())),
                "courses": sorted({term["code"] for term in where["terms"]})}

    def _student_names(self):
        """The current students' names, for the check that keeps a class's items free of any one student. A roster that
        cannot be read leaves the check to the instructions and the kinds a class is read for."""
        if self.record.students is None:
            return []
        try:
            return [student["name"] for group in self.record.students.students()["groups"] for student in group["students"] if student.get("name")]
        except Exception:  # noqa: BLE001 - the reading is still bound to its two kinds, neither about a person
            LOG.warning("Student names could not be read for a class recording's reading", exc_info=True)
            return []

    def _student_facts(self, where, lines):
        """What the reading of A STUDENT'S MEETING (step 4) is told beside the term's facts, each read through its owner: each
        student's name and section as the roster has them, the lines of their part where the recording holds several students,
        and the appointment it was filed by (its day, times, purpose and how it is recorded). No grade, note, absence,
        accommodation or message of theirs: a reading has to tell who is speaking and what a promise refers to, no more."""
        from student_support.service import appointments
        people = self.record._people({link["enrollment_id"] for link in where["students"]})
        with connect(self.path) as db:
            booked = appointments(db, [link["appointment_id"] for link in where["students"]])
        students = []
        for link in where["students"]:
            person, entry = people.get(link["enrollment_id"], {}), {}
            entry["name"] = person.get("name") or "A student no longer on a current roster"
            if person.get("sections"):
                entry["section"] = person["sections"][0]
            if link["part_start_ms"] is not None:
                inside = [line["line"] for line in lines if link["part_start_ms"] <= line["start_ms"] < link["part_end_ms"]]
                entry["lines"] = {"from": inside[0], "to": inside[-1]} if inside else None
            found = booked.get(link["appointment_id"])
            if found:
                entry["appointment"] = {"date": found["date"], "time": found["time"], "end_time": found["end_time"], "purpose": found["title"],
                                        "recorded_as": found["state"]}
            students.append(entry)
        return {"students": students}

    def _feed(self, first, last):
        """The one calendar feed between two campus dates. Google's copy is left out when it cannot be read."""
        from scheduling.feed import assemble
        with connect(self.path) as db:
            db.execute("BEGIN")
            return assemble(db, first.isoformat(), last.isoformat(), calendar=self.record.calendar, google=self.record.google,
                            on_google_error=lambda: LOG.warning("Google's calendar could not be read for a recording's reading"))

    def _calendar(self, where):
        """The calendar a reading is told, three weeks either side of the recording: Faculty's own exams, deadlines and
        entries, and the Google entries of this course (the meeting's own series, an entry naming a current term's course).
        Never a class meeting (the sections' class days say those; nor one made by hand for a redefined day), a student's appointment, a makeup or a testing center
        booking, and never another Google entry."""
        from scheduling import engine
        ours = checks.ours(where["terms"], where["basis_title"])
        found = []
        for event in self._feed(where["recorded"] - EITHER_SIDE, where["recorded"] + EITHER_SIDE):
            if engine.kind_of(event) not in ("exam", "deadline", "other") or checks.class_meeting(event) \
                    or (event.get("source") == "google" and not ours(event)):
                continue
            found.append({"date": event.get("date") or "", "time": "" if event.get("all_day") else str(event.get("time") or ""),
                          "title": " ".join(str(event.get("title") or "").split())[:160]})
        return sorted(found, key=lambda entry: (entry["date"], entry["time"], entry["title"]))[:ENTRIES]

    # -- One reading -----------------------------------------------------------------------------------------------------------
    def connection(self):
        """The Assistant's own connection with its reasoning choice, as Compute settings name it; None while the Assistant is
        off. Raises ValueError when the connection it names cannot be used."""
        from grading import config
        return config.assistant(self.frq.settings()["settings"])

    def _engine(self, payload, instructions=reading.PROMPT, schema=reading.SCHEMA):
        """(what answered, its answer) of ONE request to the Assistant's own engine. Raises Waits when it cannot be asked or did
        not answer; nothing of the request or of a credential is ever logged or kept."""
        if self.frq is None:
            raise Waits(OFF, counted=False)
        from grading.providers.base import ProviderError
        try:
            endpoint = self.connection()
        except ValueError as error:
            raise Waits(f"The Assistant's connection cannot be used, so recordings are not read: {error}", counted=False) from None
        if endpoint is None:
            raise Waits(OFF, counted=False)
        from grading import config
        # A hosted model's request has no time limit (GRADING, requirement, 2026-09-23: `cloud_call`), and a reading is one answer
        # now, never a batch job collected later (`durable` False).
        limit = None if endpoint.get("kind") in ("hosted", *config.SUBSCRIPTION_KINDS) else WAIT
        try:
            answer = self.frq.cloud_provider(endpoint, limit).submit({"prompt": instructions, "schema": schema, "payload": payload,
                                                                      "durable": False})
        except ProviderError as error:
            raise Waits(f"The Assistant's engine did not read it: {error}") from None
        except Exception as error:  # noqa: BLE001 - the reason is shown and it is tried again later
            LOG.warning("The Assistant's engine did not read a recording (%s)", type(error).__name__)
            raise Waits(f"The Assistant's engine did not read it ({type(error).__name__}).") from None
        result = answer.get("result") if isinstance(answer, dict) and isinstance(answer.get("result"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        model = metadata.get("reported_model") or metadata.get("requested_model") or endpoint.get("model") or ""
        return " · ".join(filter(None, (str(endpoint.get("name") or ""), str(model))))[:200], result.get("output")

    def _answered(self, db, recording_id, asked, **values):
        """Record how a reading ended. The Read again it answered is cleared; one asked for WHILE it was being made (another
        request, `asked` being the one this reading began with) stays wanted and is made next."""
        now = db.execute("SELECT requested_at FROM recording_reading_state WHERE recording_id=?", (recording_id,)).fetchone()
        if not now or now["requested_at"] == (asked or ""):
            values.update(requested_at="", request_id="", actor="")
        self._state(db, recording_id, **values)

    def _later(self, recording_id, tries, waits, asked=""):
        tries += int(waits.counted)
        wait = AGAIN[min(max(tries, 1), len(AGAIN)) - 1] if waits.counted else OFF_AGAIN
        with connect(self.path) as db:
            self._answered(db, recording_id, asked, tries=tries, next_try_at=iso(self.record.now() + timedelta(seconds=wait)), detail=waits.detail)

    def _read(self, recording_id):
        """Read one recording: "read", "failed" (an answer Faculty cannot use: a run that says so), "waiting" (the engine could
        not be asked or did not answer: no run), or "stale" (what was read changed meanwhile: nothing is kept, and it is due
        again)."""
        from grading.providers.local_vlm import prompt
        record = self.record
        with connect(self.path) as db:
            row, links = record._one(db, recording_id), record._links(db, recording_id)
            state = db.execute("SELECT * FROM recording_reading_state WHERE recording_id=?", (recording_id,)).fetchone()
            state = dict(state) if state else {"tries": 0, "request_id": "", "actor": "", "requested_at": ""}
            lines, where = self._lines(db, row), self._where(db, row, links)
            # A CLASS (step 5) is read by its own instructions, for the notes sections it worked through and the days the
            # instructor told it, and is told the course's notes and the titles of the section's work: no calendar, no
            # coordinator, and nothing of any student.
            lesson, dictated = where["meeting"] == "class", where["meeting"] == "memo"
            # A MEMO (step 6) is read by its own instructions for what the instructor asked for and the rules they laid down;
            # it is told no record at all.
            facts, handles = (self._memo_facts(db, lines, where), {}) if dictated else self._class_facts(db, lines, where) if lesson \
                else self._facts(db, row, lines, where)
        names = self._student_names() if lesson else ()
        if not lesson and not dictated:
            facts["calendar"] = self._calendar(where)
        # A STUDENT'S MEETING (step 4) is read by its own instructions, for what was promised and what the student said; it
        # is told who the student is and nothing of the coordinator or the Canvas copy.
        student = where["meeting"] == "student"
        instructions, version, kinds, schema = (reading.STUDENT_PROMPT, reading.STUDENT_VERSION, reading.STUDENT_KINDS, reading.SCHEMA) if student \
            else (reading.CLASS_PROMPT, reading.CLASS_VERSION, reading.CLASS_KINDS, reading.CLASS_SCHEMA) if lesson \
            else (reading.MEMO_PROMPT, reading.MEMO_VERSION, reading.MEMO_KINDS, reading.MEMO_SCHEMA) if dictated \
            else (reading.PROMPT, reading.VERSION, reading.KINDS, reading.SCHEMA)
        if student:
            facts.pop("coordinator", None)
            facts.pop("canvas", None)
            facts.update(self._student_facts(where, lines))
        payload = reading.payload(lines, facts)
        try:
            engine, output = self._engine(payload, instructions, schema)
        except Waits as waits:
            self._later(recording_id, state["tries"], waits, state["requested_at"])
            return "waiting"
        found = output if isinstance(output, dict) else reading.answer(output)
        status, begins, items, dropped, detail = "failed", None, [], [], "The engine's answer was not a reading Faculty can use."
        if isinstance(found, dict) and isinstance(found.get("items"), list):
            begins, items, dropped = reading.checked(found, lines, facts, where["recorded"], kinds, names,
                                                     most=memos.ITEMS if dictated else reading.MAX_ITEMS)
            status, detail = "read", ""
            for item in items:
                if dictated:
                    item["for"] = "you"   # a memo is the instructor's own: what it asks is theirs
                if item["about"].get("assessment"):
                    item["about"]["assessment"] = handles[item["about"]["assessment"]]
                if item["about"].get("work"):
                    item["about"]["work"] = handles[item["about"]["work"]]   # its key on the class schedule, which outlives the reading
        with record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                now = record._one(db, recording_id)
                if now["transcript_file_id"] != row["transcript_file_id"] or filed_as(record._links(db, recording_id)) != where["filed"]:
                    db.rollback()
                    return "stale"
                number = db.execute("SELECT COALESCE(MAX(run), 0) + 1 FROM recording_readings WHERE recording_id=?", (recording_id,)).fetchone()[0]
                reading_id, at = str(uuid.uuid4()), timestamp()
                db.execute("""INSERT INTO recording_readings(id, recording_id, run, transcript_file_id, filed_as, status, engine, version,
                                  prompt_sha256, input_sha256, begins_line, dropped_json, detail, request_id, actor, created_at)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (reading_id, recording_id, number, row["transcript_file_id"], where["filed"], status, engine, version,
                            reading.checksum(prompt(instructions)[0]), reading.checksum(payload), begins, encoded(dropped), detail,
                            state.get("request_id") or "", state.get("actor") or "automatic", at))
                for place, item in enumerate(items):
                    db.execute("INSERT OR IGNORE INTO recording_items(id, recording_id, item_key, created_at) VALUES (?,?,?,?)",
                               (str(uuid.uuid4()), recording_id, item["key"], at))
                    item_id = db.execute("SELECT id FROM recording_items WHERE recording_id=? AND item_key=?", (recording_id, item["key"])).fetchone()[0]
                    db.execute("""INSERT INTO recording_item_readings(reading_id, item_id, place, kind, sentence, quote, line, line_end, start_ms,
                                      end_ms, speaker_key, for_whom, day, clock, day_words, happening, about_json)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (reading_id, item_id, place, item["kind"], item["sentence"], item["quote"], item["line"], item["line_end"],
                                item["start_ms"], item["end_ms"], item["speaker_key"], item["for"], item["date"], item["time"],
                                item["date_words"], int(item["happening"]), encoded(item["about"])))
                if status == "read":
                    self._answered(db, recording_id, state["requested_at"], tries=0, next_try_at="", detail="")
                else:
                    tries = state["tries"] + 1
                    self._answered(db, recording_id, state["requested_at"], tries=tries, detail=detail,
                                   next_try_at=iso(record.now() + timedelta(seconds=AGAIN[min(tries, len(AGAIN)) - 1])))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if status == "read":
            try:
                self._cards(recording_id)
            except Exception:  # noqa: BLE001 - a card that could not be made is offered on the page; the reading stands
                LOG.exception("A recording's calendar cards could not be made")
        return status

    # -- What came out of it ---------------------------------------------------------------------------------------------------
    def _context(self, where, memo):
        """What the checks are told of one recording. `memo` lasts one read (PERFORMANCE): the feed by its dates."""
        def feed(first, last):
            key = ("feed", first, last)
            if key not in memo:
                memo[key] = self._feed(first, last)
            return memo[key]
        return {**where, "feed": feed}

    def _found(self, db, item, context, memo):
        """What Faculty's own records say of one item (recordings/checks.py), the same question asked once in a read."""
        about = item["about"]
        key = ("found", context["term_id"], context["basis_title"], context["recorded"], about.get("what"), about.get("assessment"),
               about.get("words"), item["kind"] == "date", item["kind"] == "ruling", item["date"], item["time"], item["happening"])
        if key not in memo:
            memo[key] = checks.found(db, item, context)
        return memo[key]

    def _items(self, db, run, shown, context, memo):
        """The items of one run as the page shows them, each with what became of it and, while it is open, what Faculty found
        (an item the instructor settled is not looked up in the records again)."""
        from . import classes, meetings
        lesson = context.get("class_day")
        events = {}
        for event in db.execute("""SELECT e.item_id, e.event, e.detail_json FROM recording_item_events e
                JOIN recording_item_readings x ON x.item_id=e.item_id AND x.reading_id=? ORDER BY e.rowid""", (run["id"],)):
            events.setdefault(event["item_id"], []).append((event["event"], json.loads(event["detail_json"] or "{}")))
        # A STUDENT'S MEETING (step 4): whose part an item was said in, and what their follow-ups already hold of it.
        students = context.get("students") or []
        tracked = meetings.tracked(db, [link["enrollment_id"] for link in students]) if students else {}
        # A MEMO (step 6): how each request's run by the Assistant stands, read from the Assistant's own preparations.
        dictated = context.get("meeting") == "memo"
        rows = db.execute("SELECT * FROM recording_item_readings WHERE reading_id=? ORDER BY place", (run["id"],)).fetchall()
        asked = memos.runs(db, [row["item_id"] for row in rows if row["kind"] == "todo"]) if dictated else {}
        sure, why = self._voice(db, run["recording_id"]) if dictated else (False, "")
        items, place = [], 0
        for row in rows:
            mine = events.get(row["item_id"], [])
            settled = next((event for event, _ in reversed(mine) if event in SETTLES or event == "remembered"), "reopened")
            memory = next((detail for event, detail in reversed(mine) if event in ("remembered", "reopened")), None) if settled == "remembered" else None
            ruled = next((event for event, _ in reversed(mine) if event in RULED or event == "reopened"), "")
            item = {"id": row["item_id"], "kind": row["kind"], "sentence": row["sentence"], "quote": row["quote"], "line": row["line"],
                    "start_ms": row["start_ms"], "end_ms": row["end_ms"], "at": transcript.offset(row["start_ms"]),
                    "speaker": shown.get(row["speaker_key"], "Unnamed speaker"), "for": row["for_whom"], "date": row["day"], "time": row["clock"],
                    "date_words": row["day_words"], "happening": bool(row["happening"]), "about": json.loads(row["about_json"] or "{}"),
                    "state": "open" if settled == "reopened" else "done" if settled == "remembered" else settled,
                    "memory": memory, "ruled": ruled if ruled in RULED else "",
                    # Its own card proposes a calendar entry; a student's cards (commitments, a note) are one for several items.
                    "card_id": next((detail.get("action_id") for event, detail in reversed(mine) if event == "carded" and not detail.get("card")), None)}
            # Whose it is, and the cards made for THAT student: one made for another student before a part was moved is not theirs.
            item["enrollment_id"] = meetings.owner_of(row["start_ms"], students) if students else ""
            item["cards"] = {kind: next((detail.get("action_id") for event, detail in reversed(mine) if event == "carded" and detail.get("card") == kind
                                         and detail.get("enrollment_id") == item["enrollment_id"]), None) for kind in STUDENT_CARDS}
            item["followup"] = tracked.get((item["enrollment_id"], row["item_id"]), "")
            item["when"] = when(item)
            item["assessment"] = context["assessments"].get(item["about"].get("assessment"), "")
            if not item["about"].get("assessment") and item["about"].get("what") in ("assessment", "rubric") and item["about"].get("words"):
                # Held under the name it was called by: the assessment that name is, when one is (RULINGS HELD BY NAME).
                meant = reading.named(item["about"]["words"], context["assessments"], apart=reading.name_words(context.get("code")))
                item["assessment"] = " / ".join(sorted({context["assessments"][identity] for identity in meant}))
            # What a student and the instructor said to each other is not looked up in the course's records: it goes to the
            # student's own follow-up, on a card. A CLASS (step 5): a day told to the class is set against what the section's
            # class schedule holds; what the class worked through is set against the day's plan as a whole (`class_day`).
            if dictated:
                # What a memo asks is not looked up in the records here: it is the Assistant's to carry out, on cards.
                item["found"] = None
                if item["kind"] == "todo":
                    item["ask"] = memos.standing(asked.get(row["item_id"]), sure, why, place, self.assistant is not None)
                    place += 1
                item["group"] = "done" if item["state"] != "open" else "asked" if item["kind"] == "todo" else "rules"
                item["yours"] = memos.needs_you(item)
                items.append(item)
                continue
            if lesson:
                item["found"] = self._announced(db, item, context, memo) if item["state"] == "open" and item["kind"] == "announced" else None
            else:
                item["found"] = self._found(db, item, context, memo) if item["state"] == "open" and not students else None
            item["group"] = self._group(item)
            items.append(item)
        return items

    @staticmethod
    def _announced(db, item, context, memo):
        """What the section's class schedule holds for a day told to the class (recordings/classes.py `announced`), or None.
        A record that cannot be read (a term no longer in Faculty) answers None: nothing is guessed, and neither the page
        nor the list's count fails for it."""
        from . import classes
        try:
            return classes.announced(classes.term(db, context["term_id"], memo), context["class_day"]["section_id"], item)
        except (sqlite3.Error, KeyError, TypeError, ValueError, NotFound):
            LOG.warning("A day told to a class could not be set against the class schedule", exc_info=True)
            return None

    @staticmethod
    def _group(item):
        """Where an item stands on the page: `you` (it needs the instructor), `answers`, `rulings` (held for an assessment's
        rubric), `others` (someone else owes it), or `done` (settled by the instructor, or already so by Faculty's records).
        A class's items (step 5): `covered` (what the class worked through), `you` for a day told to the class that
        DISAGREES with Faculty's records, `told` for any other day told to it, `done` once the instructor settled it."""
        if item["kind"] in reading.CLASS_KINDS:
            if item["state"] != "open":
                return "done"
            return "covered" if item["kind"] == "covered" else "you" if (item["found"] or {}).get("state") == "differs" else "told"
        if item["state"] != "open" or item["ruled"] or item.get("followup") or (item["found"] or {}).get("state") == "done":
            return "done"
        if item["kind"] == "ruling":
            return "rulings"
        if item["kind"] == "answer":
            return "answers"
        if item["kind"] == "waiting" or item["for"] in ("coordinator", "others"):
            return "others"
        return "you"

    def _card(self, action_id):
        """An item's Approve card as the Assistant's own page shows one, or None."""
        if not action_id or self.assistant is None:
            return None
        try:
            return self.assistant._view(*self.assistant.action(action_id))
        except NotFound:
            return None

    def page(self, recording_id):
        """What one recording's page shows of its reading: where the reading stands, who Faculty took as the instructor, the
        term's coordinator, and the items of its latest run, each with its quote, what became of it and what Faculty found."""
        memo = {}
        with connect(self.path) as db:
            row, links = self.record._one(db, recording_id), self.record._links(db, recording_id)
            where = self._where(db, row, links)
            readable = bool(db.execute(f"{READABLE} AND v.id=?", (row["id"],)).fetchone())
            state = db.execute("SELECT * FROM recording_reading_state WHERE recording_id=?", (row["id"],)).fetchone()
            state = dict(state) if state else {"tries": 0, "detail": "", "requested_at": ""}
            run = db.execute(f"{CURRENT} AND g.recording_id=?", (row["id"],)).fetchone() if where["filed"] else None
            last = db.execute("SELECT * FROM recording_readings WHERE recording_id=? ORDER BY run DESC LIMIT 1", (row["id"],)).fetchone()
            shown = {speaker["key"]: speaker["shown"] for speaker in self.record._speakers(db, row)[1]}
            name, you = self._you(db, shown)
            items = self._items(db, run, shown, self._context(where, memo), memo) if run else []
            coordinator = self.coordinator(db, where["term_id"])
            on_card = bool(self.on_course_card(db, where["term_id"]))
            lesson = self._lesson(db, row["id"], where, memo)
        for item in items:
            item["card"] = self._card(item.pop("card_id"))
        if lesson:
            # A CLASS (step 5): the day's comparison, its one card drawn as the Assistant's own, and whether the move may be
            # proposed by the instructor's press (it differs, or they declined it, and no card waits).
            lesson["card"] = self._card(lesson.pop("card_id"))
            lesson["offer"] = lesson.pop("move") is not None and not (lesson["card"] or {}).get("pending")
            del lesson["items"], lesson["card_ids"]
        students = self._students(where, items)
        if not where["filed"]:
            standing = {"state": "not_read", "detail": TRAINING if where["meeting"] == "training"
                        else NOT_READ if links and any(link["kind"] != "not_faculty" for link in links) else ""}
        elif run:
            failed = last["status"] == "failed" and last["run"] > run["run"]
            # `again`: a Read again waits for the worker. `changed`: Plaud changed the transcript since this reading, which is
            # read by itself; while that cannot be done, `detail` says why and Read again stays the instructor's to press.
            standing = {"state": "read", "again": bool(state["requested_at"]), "changed": run["transcript_file_id"] != row["transcript_file_id"],
                        "detail": state["detail"] or (last["detail"] if failed else ""),
                        "run": run["run"], "read_at": run["created_at"], "engine": run["engine"], "begins_line": run["begins_line"],
                        "dropped": json.loads(run["dropped_json"] or "[]")}
        elif not readable:
            standing = {"state": "none", "detail": ""}
        else:
            failed = last is not None and last["status"] == "failed"
            stopped = state["tries"] >= TRIES and not state["requested_at"]
            standing = {"state": "failed" if failed or stopped else "waiting",
                        "detail": state["detail"] or (last["detail"] if failed else ""), "stopped": stopped}
        dictated = where["meeting"] == "memo"
        groups = ("you", "covered", "told", "done") if where["meeting"] == "class" else ("you", "asked", "rules", "done") if dictated \
            else ("you", "answers", "rulings", "others", "done")
        counts = {group: sum(item["group"] == group for item in items) for group in groups}
        memo_view = None
        if dictated:
            # A MEMO (step 6): what needs the instructor is each request the Assistant is not working on and each rule still
            # open; each request shows how its run stands, with the run's cards drawn as the Assistant's own.
            counts["you"] = sum(bool(item.get("yours")) for item in items)
            on = self._assistant_on()
            for item in items:
                if item.get("ask"):
                    item["ask"]["cards"] = self._run_cards(item["ask"]["run_id"])
                    if item["ask"]["state"] in memos.WORKING and not on:
                        item["ask"]["why"] = memos.OFF   # queued, and nothing will run it: the page says so and does not ask again
            with connect(self.path) as db:
                sure, why = self._voice(db, row["id"])
            memo_view = {"sure": sure, "why": why, "assistant": on}
        if lesson and lesson["state"] == "differs" and lesson["carrier"] == row["id"]:
            counts["you"] += 1   # what was planned and not reached needs the instructor, once for the day
        return {"reading": {**standing, "readable": readable, "you": shown.get(you, ""), "your_name": name},
                "coordinator": {"term_id": where["term_id"], "name": coordinator, "term": f"{where['code']} · {where['term_label']}" if where["term_id"] else "",
                                # COORDINATOR "ONE NAME": said only when the name is the course card's, where it is changed.
                                **({"card": True} if on_card else {})},
                "items": items, "counts": counts, "meeting": where["meeting"], "students": students,
                **({"class_day": lesson} if where["meeting"] == "class" else {}), **({"memo": memo_view} if dictated else {})}

    # -- A class (step 5) ---------------------------------------------------------------------------------------------------
    def _lesson(self, db, recording_id, where, memo):
        """A class recording's class day as its page shows it (recordings/classes.py `view`), with the move Faculty would
        propose now; None for any other recording. A term the class schedule can no longer read leaves it out."""
        from . import classes
        if where["meeting"] != "class" or not where["term_id"]:
            return None
        try:
            return classes.view(classes.term(db, where["term_id"], memo), where["class_day"]["section_id"], where["class_day"]["date"], recording_id)
        except (sqlite3.Error, KeyError, TypeError, ValueError, NotFound):
            LOG.warning("A class recording's class day could not be set against the class schedule", exc_info=True)
            return None

    def _propose_move(self, recording_id, lesson, items, request_id="", actor="automatic"):
        """ONE Approve card for a class: what the class did not reach of the day's planned topics moves to that section's
        next class day. It goes through the Assistant's own actions and the class schedule's own route, which previews the
        change as saving it would and refuses a stale revision, and it waits for the instructor's Approve like every
        background proposal: the plan never changes by itself. Each `covered` item it rests on records it."""
        wanted = lesson["move"]
        moved = [f"{entry['number']} {entry['title']}".strip() for entry in lesson["not_reached"]]
        reached = ", ".join(entry["number"] for entry in lesson["reached"])
        body = {"request_id": str(uuid.uuid4()), "base_revision": wanted["base_revision"], "set": [], "remove": [], "topics": wanted["topics"]}
        session = self.assistant.open_session(engine="recordings", label="What a recorded class did not reach")
        session["_proposal_only"] = True
        try:
            made = self.assistant.write(
                session, "POST", f"/api/scheduling/terms/{wanted['term_id']}/class-schedule", body,
                f"Move {', '.join(moved)} from {lesson['day']} to {lesson['next']} on Section {lesson['section']}'s class schedule: "
                f"the recording of that class reached {reached}.", page=f"#recordings/{recording_id}")   # the day's one recording that carries it
        finally:
            self.assistant.close_session(session["id"])
        if made.get("outcome") != "waiting_for_approval" or not made.get("action_id"):
            raise Conflict(str(made.get("error") or made.get("note") or "The move could not be proposed."))
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for item_id in items:
                    self._event(db, item_id, "carded", {"action_id": made["action_id"], "card": MOVE, "section_id": lesson["section_id"],
                                                        "date": lesson["date"]}, request_id, actor)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return made["action_id"]

    def _class_cards(self, recording_id, request_id="", actor="automatic", pressed=False):
        """After the reading of a class, after anything it rests on changed, and on the instructor's own press: the day's ONE
        move card. A card that still waits and no longer says what Faculty would propose now (the plan was edited, another
        section's move was approved, an item was dismissed) is taken back, and the move is proposed as it stands now. Once:
        one the instructor declined or let lapse is proposed again only by their press (`pressed`). The card is the DAY's:
        any of two recordings of one class may raise it, and an earlier card of the day that still waits (two made in one
        instant by the worker and a press) is taken back. A move the class schedule would refuse is never proposed, so a
        card is never taken back for one that cannot be made."""
        from . import classes
        if self.assistant is None:
            return
        memo = {}
        with connect(self.path) as db:
            row, links = self.record._one(db, recording_id), self.record._links(db, recording_id)
            where = self._where(db, row, links)
            lesson = self._lesson(db, row["id"], where, memo) if db.execute(f"{CURRENT} AND g.recording_id=?", (row["id"],)).fetchone() else None
        if not lesson:
            if pressed:
                raise Conflict("This is not a class recording Faculty has read.")
            return
        card, wanted = self._card(lesson["card_id"]), lesson["move"]
        self._withdraw(lesson["card_ids"][:-1], "A later card proposes this class day's move.")
        taken_back = card is None or self._taken_back(card)
        if card and card["pending"]:
            same = wanted is not None and card["body"].get("topics") == wanted["topics"] and card["body"].get("base_revision") == wanted["base_revision"]
            if same:
                if pressed:
                    raise Conflict("Its card is already waiting for you.")
                return
            self._withdraw([card["id"]], "The class schedule or the reading changed since this move was proposed.")
            taken_back = True
        if wanted is None:
            if pressed:
                raise Conflict(lesson["blocked"] or "Nothing planned for this day is unreached.")
            return
        if pressed or (taken_back and lesson["state"] == "differs"):
            # What it rests on: every `covered` item of the day's recordings, which say where the class got to.
            self._propose_move(lesson["carrier"], lesson, sorted({item for found in lesson["items"].values() for item in found}), request_id, actor)

    def refresh_moves(self):
        """Each round: the move cards made lately are looked at again, so one that waits on a class schedule that has changed
        since (the instructor approved another section's move, or edited the topics) is replaced by the move as it stands
        now, and one with nothing left to move is taken back. Nothing here writes the plan."""
        if self.assistant is None:
            return
        since = (datetime.now(timezone.utc) - MOVES_SINCE).strftime("%Y-%m-%dT%H:%M:%S")   # an event's time is the machine's own (`timestamp`)
        with connect(self.path) as db:
            lately = [row[0] for row in db.execute(
                """SELECT DISTINCT i.recording_id FROM recording_item_events e JOIN recording_items i ON i.id=e.item_id
                   WHERE e.event='carded' AND json_extract(e.detail_json,'$.card')=? AND e.created_at>=?""", (MOVE, since))]
        for recording_id in lately:
            try:
                with self._pressing:
                    self._class_cards(recording_id)
            except Exception:  # noqa: BLE001 - the card stays as it is; its own Approve refuses a stale plan
                LOG.warning("A class recording's move card could not be looked at again", exc_info=True)

    # -- A student's meeting (step 4) ---------------------------------------------------------------------------------------
    OFF = {"cancelled": "cancelled", "no_show": "a no-show"}

    def _students(self, where, items):
        """The students of a student's meeting as its page shows them, in the order they came: who each is, their part, the
        appointment the recording was filed by and whether that appointment is recorded as one that did not happen (orange:
        Faculty changes neither), their two cards, and what may still be proposed. Names come from the verified roster files
        and the appointment from its owner, by reference."""
        if not where["students"]:
            return []
        from student_support.service import appointments
        from . import meetings
        people = self.record._people({link["enrollment_id"] for link in where["students"]})
        with connect(self.path) as db:
            booked = appointments(db, [link["appointment_id"] for link in where["students"]])
        found = []
        for link in where["students"]:
            mine = [item for item in items if item["enrollment_id"] == link["enrollment_id"]]
            person, appointment = people.get(link["enrollment_id"], {}), booked.get(link["appointment_id"])
            cards = {kind: self._card(next((item["cards"][kind] for item in reversed(mine) if item["cards"][kind]), None)) for kind in STUDENT_CARDS}
            off = ""
            if appointment and appointment["state"] in self.OFF:
                off = (f"Their {display_clock(appointment['time'])} appointment that day is recorded as {self.OFF[appointment['state']]}, yet this "
                       "recording began in it. Correct the appointment if they came, or file this recording elsewhere.")
            promised = [item for item in mine if item["kind"] in meetings.PROMISES and item["state"] == "open" and not item["followup"]]
            found.append({"enrollment_id": link["enrollment_id"], "name": person.get("name") or "A student", "sections": person.get("sections") or [],
                          "part": {"start_ms": link["part_start_ms"], "end_ms": link["part_end_ms"]} if link["part_start_ms"] is not None else None,
                          "appointment": {key: appointment[key] for key in ("state", "date", "time", "end_time")} if appointment else None,
                          "off": off, "cards": cards, "items": len(mine), "on_followup": sum(1 for item in mine if item["followup"]),
                          "offer": {"commitments": bool(promised) and not (cards["commitments"] or {}).get("pending"),
                                    "note": any(item["state"] != "dismissed" for item in mine) and not (cards["note"] or {}).get("pending")
                                            and (cards["note"] or {}).get("status") != "succeeded"}})
        return found

    def _followup(self, enrollment_id):
        """The student's open follow-up a commitment is proposed on, as the follow-up service's own listing answers it (its
        latest revision, the student's messages it has seen, its commitments): the one changed last when several are open, or
        None when none is. A resolved follow-up is not opened again by a recording: a new one is."""
        found = [item for item in self.support.listing(kind="followup", enrollment_id=enrollment_id)["items"]
                 if item.get("id") and item["state"] != "resolved"]
        return max(found, key=lambda item: (item["updated_at"], item["id"])) if found else None

    @staticmethod
    def _commitment(item, today):
        """One promise as a commitment of a follow-up (TEACHING-WORKFLOWS "SEPARATE COMMITMENTS"): its sentence, who owes the
        next step, the day that was said where one was (a check back the instructor records by approving the card), and the
        recording item it was heard in. Its identity is the item's, so the same promise is never tracked twice."""
        mine = item["kind"] == "todo" and item["for"] in ("you", "unclear")
        found = {"id": f"rec-{item['id']}", "title": item["sentence"][:200], "kind": "work", "state": "open",
                 "waiting_on": "you" if mine else "student", "recording_item_id": item["id"]}
        if item["kind"] == "date" and item["date"]:
            found.update(kind="check_back", waiting_on="date", check_back=item["date"])
        elif item["kind"] == "date":
            found.update(waiting_on="you")
        elif item["date"] and item["date"] >= today:
            found["check_back"] = item["date"]
        return found

    def _write_card(self, recording_id, kind, enrollment_id, items, label, path, body, summary, request_id, actor):
        """ONE Approve card of a student's meeting, through the Assistant's own actions and the owning module's own route; it
        waits for the instructor's Approve like every background proposal. Each item it was made from records it."""
        session = self.assistant.open_session(engine="recordings", label=label)
        session["_proposal_only"] = True
        try:
            made = self.assistant.write(session, "POST", path, body, summary, page=f"#recordings/{recording_id}?t={items[0]['start_ms']}")
        finally:
            self.assistant.close_session(session["id"])
        if made.get("outcome") != "waiting_for_approval" or not made.get("action_id"):
            raise Conflict(str(made.get("error") or made.get("note") or "The card could not be made."))
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for item in items:
                    self._event(db, item["id"], "carded", {"action_id": made["action_id"], "card": kind, "enrollment_id": enrollment_id}, request_id, actor)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return made["action_id"]

    def _propose_commitments(self, recording_id, student, items, where, request_id="", actor="automatic"):
        """PROMISES HEARD BECOME ONE CARD proposing commitments on that student's follow-up, opening a follow-up where none is
        open. On an open follow-up the card sends what its own page would: its title, state and words as they stand, the
        student's messages it has seen, the revision it was read at (changed since, Approve is refused and nothing is
        written), every commitment it holds and the new ones after them."""
        today = filing.campus(self.record.now()).date().isoformat()
        target, day = self._followup(student["enrollment_id"]), day_label(where["recorded"])
        held = [{key: value for key, value in commitment.items() if key not in ("effective_state", "heard")} for commitment in (target or {}).get("commitments") or []]
        new = [found for found in (self._commitment(item, today) for item in items) if found["id"] not in {commitment["id"] for commitment in held}]
        if not new:
            raise Conflict("Their follow-up already holds everything that was promised in this meeting.")
        if target:
            # Its state as it is saved, with two exceptions that keep the card from saying more than it does: a student message
            # that still needs a reply (the save also records what the follow-up has seen) stays Needs reply, and a follow-up
            # saved as resolved, open again only for its tasks, is To do.
            saved = target.get("saved_state") or target["state"]
            state = "needs_reply" if target["state"] == "needs_reply" else saved if saved in ("needs_reply", "todo", "waiting", "snoozed") else "todo"
            body = {"kind": "followup", "enrollment_id": student["enrollment_id"], "source_key": target.get("source_key") or "",
                    "title": target["title"], "details": target.get("details") or "", "date": target.get("date") or "",
                    **({"next": target["next"]} if target.get("next") else {}),
                    "state": state,
                    "observed": target.get("observed") or [], "commitments": held + new, "base_revision": target["revision"]}
            path, where_to = f"/api/student-support/{target['id']}", f"their follow-up “{target['title'][:80]}”"
        else:
            owed = any(commitment["waiting_on"] == "you" for commitment in new)
            body = {"kind": "followup", "enrollment_id": student["enrollment_id"], "title": f"Meeting on {day}",
                    "state": "todo" if owed else "waiting", "details": "", "date": "", "commitments": new}
            path, where_to = "/api/student-support", "a new follow-up"
        body["request_id"] = str(uuid.uuid4())
        count = f"{len(new)} thing{'' if len(new) == 1 else 's'}"
        summary = f"Track {count} said in your meeting with {student['name']} on {day} on {where_to}."
        return self._write_card(recording_id, "commitments", student["enrollment_id"], items, "Promises said in a student's recorded meeting",
                                path, body, summary, request_id, actor)

    def _propose_note(self, recording_id, student, items, where, request_id="", actor="automatic"):
        """A PRIVATE NOTE about the student (STUDENT-NOTES), proposed on ONE card: what the reading kept of their part of the
        meeting, in plain lines, dated the day of the recording. Saved only by Approve; the student never sees it."""
        from . import meetings
        kept = [item for item in items if item["state"] != "dismissed"]
        text = f"Recorded meeting on {day_label(where['recorded'])}.\n{meetings.said(kept)}"[:5000]
        body = {"enrollment_id": student["enrollment_id"], "date": where["recorded"].isoformat(), "text": text, "request_id": str(uuid.uuid4())}
        return self._write_card(recording_id, "note", student["enrollment_id"], kept, "A note of a student's recorded meeting", "/api/student-notes", body,
                                f"Add a private note about {student['name']}: what was said in your meeting on {day_label(where['recorded'])}.",
                                request_id, actor)

    def _taken_back(self, card):
        last = ((card or {}).get("events") or [{}])[-1]
        return last.get("kind") == "expired" and bool((last.get("detail") or {}).get("superseded"))

    def _student_cards(self, recording_id, view, where):
        """After the reading of a student's meeting (and after it is filed elsewhere): for each student, ONE card for what was
        promised and ONE for a note. Once: a card the instructor declined or let lapse is proposed again only on their own
        press; one Faculty itself took back is made again. Promises a later reading added join the card that still waits (it
        is taken back and one card made for all of them)."""
        from . import meetings
        if self.support is None:
            return
        for student in view["students"]:
            mine = [item for item in view["items"] if item["enrollment_id"] == student["enrollment_id"]]
            cards = {action: self._card(action) for action in {item["cards"][kind] for item in mine for kind in STUDENT_CARDS} - {None}}
            promised = [item for item in mine if item["kind"] in meetings.PROMISES and item["state"] == "open" and not item["followup"]]
            fresh = [item for item in promised if not item["cards"]["commitments"] or self._taken_back(cards.get(item["cards"]["commitments"]))]
            waiting = (student["cards"]["commitments"] or {}) if (student["cards"]["commitments"] or {}).get("pending") else None
            try:
                if fresh:
                    together = fresh + [item for item in promised if item not in fresh and waiting and item["cards"]["commitments"] == waiting["id"]]
                    if waiting:
                        self._withdraw([waiting["id"]], "A later reading of the recording holds more that was promised: one card is made for all of it.")
                    self._propose_commitments(recording_id, student, sorted(together, key=lambda item: item["start_ms"]), where)
            except Exception:  # noqa: BLE001 - offered on the page instead
                LOG.warning("The commitments said in a student's recorded meeting could not be proposed", exc_info=True)
            kept = [item for item in mine if item["state"] != "dismissed"]
            noted = [item["cards"]["note"] for item in mine if item["cards"]["note"]]
            try:
                if kept and (not noted or all(self._taken_back(cards.get(action)) for action in noted)):
                    self._propose_note(recording_id, student, kept, where)
            except Exception:  # noqa: BLE001 - offered on the page instead
                LOG.warning("The note of a student's recorded meeting could not be proposed", exc_info=True)

    def student_card(self, recording_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/cards`: propose again ONE of a student's two cards (`kind`: `commitments`, what was
        promised and no follow-up holds yet, or `note`), naming the student where the recording holds several. Only what
        Approve on the card then does is written. A repeated request ID proposes nothing again."""
        data = data if isinstance(data, dict) else {}
        request_id, kind = valid_request_id(data.get("request_id")), data.get("kind")
        if kind == MOVE:
            return self.move_card(recording_id, request_id, actor)
        if kind not in STUDENT_CARDS:
            raise ValueError("Say which card: what was promised (commitments), a note, or for a class the move of what was not reached (move).")
        if self.assistant is None or self.support is None:
            raise Conflict("The Assistant is not available, so no card can be made.")
        with self._pressing:
            with connect(self.path) as db:
                row, links = self.record._one(db, recording_id), self.record._links(db, recording_id)
                where = self._where(db, row, links)
                repeated = db.execute("SELECT 1 FROM recording_item_events WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
            if not repeated:
                from . import meetings
                view = self.page(recording_id)
                wanted = data.get("enrollment_id") or (view["students"][0]["enrollment_id"] if len(view["students"]) == 1 else None)
                student = next((student for student in view["students"] if student["enrollment_id"] == wanted), None)
                if student is None:
                    raise ValueError("Say which student of this recording.")
                if (student["cards"][kind] or {}).get("pending"):
                    raise Conflict("Its card is already waiting for you.")
                mine = [item for item in view["items"] if item["enrollment_id"] == student["enrollment_id"]]
                if kind == "commitments":
                    promised = [item for item in mine if item["kind"] in meetings.PROMISES and item["state"] == "open" and not item["followup"]]
                    if not promised:
                        raise Conflict("Nothing that was promised in their part of this meeting is left to track.")
                    self._propose_commitments(row["id"], student, promised, where, request_id, actor)
                else:
                    kept = [item for item in mine if item["state"] != "dismissed"]
                    if not kept:
                        raise Conflict("The reading kept nothing of their part of this meeting to note.")
                    if (student["cards"]["note"] or {}).get("status") == "succeeded":
                        raise Conflict("A note of this meeting is already saved. Change it in their notes.")
                    self._propose_note(row["id"], student, kept, where, request_id, actor)
        return self.record.recording(recording_id)

    def move_card(self, recording_id, request_id, actor="workspace"):
        """`POST /api/recordings/<id>/cards` with `kind` `move` (step 5): propose again that what a class did not reach of its
        day's planned topics moves to the section's next class day (its card was declined, lapsed, or could not be made).
        Only what Approve on the card then does is written. A repeated request ID proposes nothing again."""
        if self.assistant is None:
            raise Conflict("The Assistant is not available, so no card can be made.")
        with self._pressing:
            with connect(self.path) as db:
                self.record._one(db, recording_id)
                repeated = db.execute("SELECT 1 FROM recording_item_events WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
            if not repeated:
                self._class_cards(recording_id, request_id, actor, pressed=True)
        return self.record.recording(recording_id)

    def for_you(self, only=None):
        """{recording ID: how many of its items need the instructor now}: the open items for them of each recording's latest
        reading that Faculty's own records do not show as already done. One memo for the whole count."""
        memo, counts = {}, {}
        with connect(self.path) as db:
            runs = [dict(run) for run in db.execute(CURRENT + (" AND g.recording_id=?" if only else ""), (only,) if only else ())]
            for run in runs:
                # Settled items are not looked up in the records: only what is still open for the instructor is checked.
                # A class (step 5): its day counts once where a planned section was not reached, and each day told to the
                # class that disagrees with Faculty's records counts.
                lesson = run["filed_as"].startswith("class_day:")
                unsettled = db.execute("""SELECT COUNT(*) FROM recording_item_readings x WHERE x.reading_id=? AND (x.kind='announced'
                    OR (x.kind IN ('todo','date','policy') AND x.for_whom IN ('you','unclear')))""", (run["id"],)).fetchone()[0]
                if not unsettled and not lesson:
                    continue
                row, links = self.record._one(db, run["recording_id"]), self.record._links(db, run["recording_id"])
                where = self._where(db, row, links)
                count = sum(item["group"] == "you" or bool(item.get("yours")) for item in self._items(db, run, {}, self._context(where, memo), memo)) if unsettled else 0
                day = self._lesson(db, row["id"], where, memo) if lesson else None
                count += int(bool(day) and day["state"] == "differs" and day["carrier"] == row["id"])
                if count:
                    counts[run["recording_id"]] = count
        return counts

    # -- What became of an item ------------------------------------------------------------------------------------------------
    def _item(self, db, recording_id, item_id):
        """(the recording's row, where it is filed, its current run, the item as that run read it) or NotFound."""
        row, links = self.record._one(db, recording_id), self.record._links(db, recording_id)
        where = self._where(db, row, links)
        run = db.execute(f"{CURRENT} AND g.recording_id=?", (row["id"],)).fetchone() if where["filed"] else None
        read = db.execute("SELECT * FROM recording_item_readings WHERE reading_id=? AND item_id=?", (run["id"], str(item_id))).fetchone() if run else None
        if not read:
            raise NotFound("This item is not in the recording's latest reading.")
        return row, where, run, read

    @staticmethod
    def _event(db, item_id, event, detail, request_id, actor):
        db.execute("INSERT INTO recording_item_events(id, item_id, event, detail_json, request_id, actor, created_at) VALUES (?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), item_id, event, encoded(detail), request_id, str(actor)[:100], timestamp()))

    @staticmethod
    def _repeated(db, request_id, item_id):
        """Whether this request ID already did its work on this item; one used for another item is refused."""
        earlier = db.execute("SELECT item_id FROM recording_item_events WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
        if earlier and earlier["item_id"] != item_id:
            raise Conflict("This request ID already settled another item.")
        return bool(earlier)

    def settle(self, recording_id, item_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/items/<item>`: the instructor says an item is done, dismisses it or opens it again; or, at
        an assessment's rubric finalization, accepts or declines a ruling (`assignment`: the graded assignment it was offered
        on; never one that is certified). An event; nothing else in Faculty changes. A repeated request ID does nothing again."""
        data = data if isinstance(data, dict) else {}
        request_id, state = valid_request_id(data.get("request_id")), data.get("state")
        if state not in SETTLES + RULED:
            raise ValueError("Say what became of it: done, dismissed, reopened, or for a ruling accepted or declined.")
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                _, where, _, read = self._item(db, recording_id, item_id)
                if not self._repeated(db, request_id, read["item_id"]):
                    detail = {}
                    if state in RULED:
                        if read["kind"] != "ruling":
                            raise ValueError("Only a ruling is accepted or declined for a rubric.")
                        plan = self._plan(db, data.get("assignment"))
                        if not self._of(plan, json.loads(read["about_json"] or "{}"), *self._names(db, plan)):
                            raise ValueError("This ruling was said of another assessment.")
                        if plan["certified"]:
                            raise Conflict("This assessment is certified: a ruling from a recording is never offered on a certified plan (RECORDINGS).")
                        detail = {"assignment": plan["id"]}
                    self._event(db, read["item_id"], state, detail, request_id, actor)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if where["meeting"] == "memo" and read["kind"] == "todo" and self.assistant is not None:
            # A memo's request dismissed: its run by the Assistant and the cards that still wait are withdrawn. Opened again,
            # one Faculty never asked is asked now.
            try:
                with self._pressing:
                    self._memo_sync(recording_id)
            except Exception:  # noqa: BLE001 - the item is settled; an approval of a withdrawn request is refused by its own guard
                LOG.warning("A dictated request's run could not follow its settled item", exc_info=True)
        if read["kind"] == "covered" and self.assistant is not None:
            # A `covered` item dismissed or opened again changes what the class reached: its day's move card follows.
            try:
                with self._pressing:
                    self._class_cards(recording_id)
            except Exception:  # noqa: BLE001 - the item is settled; the card is looked at again each round
                LOG.warning("A class recording's move card could not follow a settled item", exc_info=True)
        return self.record.recording(recording_id)

    def remember(self, recording_id, item_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/items/<item>/remember`: the instructor's own press of Remember on a `policy` item keeps it
        as an Assistant memory, in the words shown (`content`: theirs to edit first), for the recording's term unless they widen
        it (`scope`: `term`, `course` or `workspace`). The memory's own checks apply (ASSISTANT-MEMORY "EVERY WAY IN": never a
        statement naming a student, never a record's fact). Its source is `recording`; the recording and the quote it rests on
        stay here, with the item."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        if self.assistant is None:
            raise Conflict("The Assistant's memory is not available.")
        with self._pressing:
            return self._remember(recording_id, item_id, data, request_id, actor)

    def _remember(self, recording_id, item_id, data, request_id, actor):
        with connect(self.path) as db:
            _, where, _, read = self._item(db, recording_id, item_id)
            if self._repeated(db, request_id, read["item_id"]):
                return self.record.recording(recording_id)
            if read["kind"] != "policy":
                raise ValueError("Only a standing rule said in a meeting is kept as a memory.")
            # Once for an item while that memory stands, opened again or not: a second press would keep the same rule twice.
            for event in db.execute("SELECT detail_json FROM recording_item_events WHERE item_id=? AND event='remembered' ORDER BY rowid DESC",
                                    (read["item_id"],)):
                standing = db.execute("SELECT status FROM assistant_memory_latest WHERE memory_id=?",
                                      (json.loads(event["detail_json"]).get("memory_id"),)).fetchone()
                if standing and standing["status"] == "current":
                    raise Conflict("This is already kept as a memory. Change or remove it on the Memory page.")
        scope = data.get("scope", "term" if where["term_id"] else "course" if where["definition_id"] else "workspace")
        owner = {"term": {"scope": "term", "term_id": where["term_id"]}, "course": {"scope": "course", "definition_id": where["definition_id"]},
                 "workspace": {"scope": "workspace"}}.get(scope if isinstance(scope, str) else "")
        if owner is None or (scope == "term" and not where["term_id"]) or (scope == "course" and not where["definition_id"]):
            raise ValueError("Keep it for this term, for the course in every term, or for everything.")
        memory = self.assistant.memory.write({"content": data.get("content", read["sentence"]), **owner}, source="recording")
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._event(db, read["item_id"], "remembered", {"memory_id": memory["id"], "revision": memory["revision"], "scope": scope},
                            request_id, actor)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.record.recording(recording_id)

    def set_coordinator(self, data, actor="workspace"):
        """`POST /api/recordings/coordinator`: a term's coordinator, a name alone (an empty name takes it back). TERM-owned; a
        revision. A reading made afterwards is told it; nothing is read again by itself. COORDINATOR "ONE NAME": a term whose
        course card names its coordinator takes no other name here (409): the card's is changed in Course settings."""
        data = data if isinstance(data, dict) else {}
        request_id, term_id, name = valid_request_id(data.get("request_id")), data.get("term_id"), data.get("name", "")
        if not isinstance(term_id, str) or not isinstance(name, str):
            raise ValueError("Say which term, and the coordinator's name.")
        name = " ".join(name.split())
        if len(name) > 100:
            raise ValueError("A coordinator's name is at most 100 characters.")
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if not db.execute("SELECT 1 FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone():
                    raise NotFound("This term is no longer in Faculty.")
                if self.on_course_card(db, term_id):
                    raise Conflict("COORDINATOR: this term's coordinator is named on its course card. Change it in Course settings.")
                earlier = db.execute("SELECT term_id FROM recording_coordinators WHERE request_id=?", (request_id,)).fetchone()
                if earlier and earlier["term_id"] != term_id:
                    raise Conflict("This request ID already named another term's coordinator.")
                if not earlier and name != self.coordinator(db, term_id):
                    number = db.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM recording_coordinators WHERE term_id=?", (term_id,)).fetchone()[0]
                    db.execute("INSERT INTO recording_coordinators(term_id, revision, name, request_id, actor, created_at) VALUES (?,?,?,?,?,?)",
                               (term_id, number, name, request_id, str(actor)[:100], timestamp()))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return {"term_id": term_id, "name": name}

    # -- Approve cards for calendar entries -------------------------------------------------------------------------------------
    def _propose(self, recording_id, item, where, request_id="", actor="automatic"):
        """One Approve card proposing a calendar entry for something said to happen on a day. It goes through the Assistant's
        own actions and the Calendar's own route, and waits for the instructor's Approve like every background proposal."""
        title = item["about"].get("words") or item["sentence"]
        owner = ({"scope": "term", "definition_id": where["definition_id"], "term_id": where["term_id"]} if where["term_id"]
                 else {"scope": "course", "definition_id": where["definition_id"]} if where["definition_id"] else {"scope": "workspace"})
        body = {"title": title[:160], "date": item["date"], "time": item["time"], "all_day": not item["time"], "category": "event",
                "notes": f"Said in the meeting recorded {day_label(where['recorded'])}, by {item['speaker']}: “{item['quote']}”"[:4000],
                **owner, "request_id": str(uuid.uuid4())}
        session = self.assistant.open_session(engine="recordings", label="A date said in a recorded meeting")
        session["_proposal_only"] = True
        try:
            made = self.assistant.write(session, "POST", "/api/calendar/events", body,
                                        f"Add “{title[:120]}” on {when(item)} to your calendar: it was said in the meeting recorded {day_label(where['recorded'])}.",
                                        page=f"#recordings/{recording_id}?t={item['start_ms']}")
        finally:
            self.assistant.close_session(session["id"])
        if made.get("outcome") != "waiting_for_approval" or not made.get("action_id"):
            raise Conflict(str(made.get("error") or "The calendar entry could not be proposed."))
        with self.record.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._event(db, item["id"], "carded", {"action_id": made["action_id"]}, request_id, actor)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return made["action_id"]

    def _withdraw(self, action_ids, why):
        """Take back Approve cards that still wait (an item the newest reading no longer holds, a recording filed elsewhere)."""
        if self.assistant is None:
            return
        for action_id in action_ids:
            with self.assistant._approve_lock:
                try:
                    _, events = self.assistant.action(action_id)
                except NotFound:
                    continue
                if events and events[-1]["kind"] in ("proposed", "previewed"):
                    self.assistant._event(action_id, "expired", "faculty", {"error": why, "superseded": True})

    def _cards(self, recording_id):
        """After a reading, and after a recording is filed elsewhere: an Approve card for each thing said to happen on a day,
        today or later, that is not on the calendar and has no card yet; cards of items the reading no longer holds are taken
        back."""
        if self.assistant is None:
            return
        view = self.page(recording_id)
        with connect(self.path) as db:
            row, links = self.record._one(db, recording_id), self.record._links(db, recording_id)
            where = self._where(db, row, links)
            # Whose each held item is now: a student's card made when an item was theirs is not theirs to approve once the
            # item is another student's or nobody's (a part was moved, or the recording was filed under fewer students).
            held = {item["id"]: item["enrollment_id"] for item in view["items"]}
            gone = [detail.get("action_id") for item_id, detail in (
                (event["item_id"], json.loads(event["detail_json"])) for event in db.execute(
                    """SELECT e.item_id, e.detail_json FROM recording_item_events e JOIN recording_items i ON i.id=e.item_id
                       WHERE i.recording_id=? AND e.event='carded'""", (row["id"],)))
                if item_id not in held or (detail.get("card") in STUDENT_CARDS and detail.get("enrollment_id") != held[item_id])]
        self._withdraw([action for action in gone if action], "The recording was read again and no longer holds this." if where["filed"]
                       else "The recording is no longer filed where this was read.")
        today = filing.campus(self.record.now()).date().isoformat()
        for item in view["items"]:
            # Once for an item: a card the instructor declined or let lapse is proposed again only on their own press. One that
            # Faculty itself took back (the recording was filed elsewhere, then read where it is now) is made again.
            last = ((item["card"] or {}).get("events") or [{}])[-1]
            taken_back = last.get("kind") == "expired" and (last.get("detail") or {}).get("superseded")
            if item["group"] == "you" and (item["found"] or {}).get("offer") == "calendar" and (not item["card"] or taken_back) and item["date"] >= today:
                try:
                    self._propose(row["id"], item, where)
                except Exception:  # noqa: BLE001 - offered on the page instead
                    LOG.warning("A calendar entry said in a recording could not be proposed", exc_info=True)
        if where["meeting"] == "student":
            # One at a time with the instructor's own press (`student_card`), so the two never make the same card twice here.
            # (The worker is another process: what it and a press could both propose in one instant is refused when the
            # second is approved, since a follow-up never holds what another follow-up of the student already tracks.)
            with self._pressing:
                self._student_cards(row["id"], self.page(recording_id), where)
        if where["meeting"] == "class":
            # A CLASS (step 5): ONE card, for what the class did not reach of the day's planned topics.
            try:
                with self._pressing:
                    self._class_cards(row["id"])
            except Exception:  # noqa: BLE001 - offered on the page instead
                LOG.warning("The move of what a recorded class did not reach could not be proposed", exc_info=True)
        # A MEMO (step 6): each request is put to the Assistant, once; and whatever the recording is now, a run of a request it
        # no longer holds as a memo is withdrawn.
        try:
            with self._pressing:
                self._memo_sync(row["id"])
        except Exception:  # noqa: BLE001 - offered on the page instead (Ask the Assistant)
            LOG.warning("A memo's requests could not be put to the Assistant", exc_info=True)

    def parts_changed(self, recording_id):
        """The students of a recording, or their parts, were set (step 4): a student's cards rest on which items are theirs, so
        the ones that still wait are taken back, and after that they are made again for the parts as they are now."""
        try:
            with connect(self.path) as db:
                waiting = {json.loads(event["detail_json"]).get("action_id") for event in db.execute(
                    """SELECT e.detail_json FROM recording_item_events e JOIN recording_items i ON i.id=e.item_id
                       WHERE i.recording_id=? AND e.event='carded' AND json_extract(e.detail_json,'$.card') IS NOT NULL""", (recording_id,))}
            self._withdraw(sorted(action for action in waiting if action), "The students' parts of this recording were changed.")
            self._cards(recording_id)
        except Exception:  # noqa: BLE001 - the parts stand
            LOG.exception("A recording's cards could not be made again for its students' parts")

    def refiled(self, recording_id):
        """A recording was filed elsewhere by hand: cards of what was read where it was filed before are taken back."""
        try:
            self._cards(recording_id)
        except Exception:  # noqa: BLE001 - the filing stands
            LOG.exception("A re-filed recording's cards could not be taken back")

    def gone(self, recording_id):
        """A recording is NOT KEPT and is about to be erased (RECORDINGS "ERASED WHEN IT DOES NOT BELONG"): every Approve card
        made from it that still waits is taken back, and every run of a memo's request is withdrawn with its waiting cards.
        What the instructor approved stands. Called before the rows that name those cards leave the catalog."""
        why = "This recording is no longer kept."
        try:
            with connect(self.path) as db:
                items = [row["id"] for row in db.execute("SELECT id FROM recording_items WHERE recording_id=?", (recording_id,))]
                cards = sorted({action for action in (json.loads(event["detail_json"]).get("action_id") for event in db.execute(
                    """SELECT e.detail_json FROM recording_item_events e JOIN recording_items i ON i.id=e.item_id
                       WHERE i.recording_id=? AND e.event='carded'""", (recording_id,))) if action})
                runs = memos.runs(db, items)
            self._withdraw(cards, why)
            if self.assistant is not None:
                for item_id, run in runs.items():
                    if run["state"] != "stale":
                        self.assistant.workflows.dictated_gone(item_id, why)
        except Exception:  # noqa: BLE001 - the recording is erased all the same; a card left waiting names a page that is gone
            LOG.exception("The cards of a recording that is not kept could not be taken back")

    def card(self, recording_id, item_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/items/<item>/card`: propose the calendar entry of an item again (its card expired after a
        day, or could not be made). The entry is added only by Approve on the card."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        if self.assistant is None:
            raise Conflict("The Assistant is not available, so no card can be made.")
        with self._pressing:
            self._card_again(recording_id, item_id, request_id, actor)
        return self.record.recording(recording_id)

    def _card_again(self, recording_id, item_id, request_id, actor):
        with connect(self.path) as db:
            row, where, _, read = self._item(db, recording_id, item_id)
            repeated = self._repeated(db, request_id, read["item_id"])
        if not repeated:
            item = next(item for item in self.page(recording_id)["items"] if item["id"] == read["item_id"])
            if (item["card"] or {}).get("pending"):
                raise Conflict("Its card is already waiting for you.")
            if (item["found"] or {}).get("offer") != "calendar":
                raise Conflict("Nothing is left to add: " + ((item["found"] or {}).get("text") or "this item names no day that is missing from your calendar."))
            self._propose(row["id"], item, where, request_id, actor)

    # -- A memo (step 6): what it asks goes to the Assistant ------------------------------------------------------------------------
    GONE = "RECORDINGS: this is no longer a request of a memo of yours (the recording was filed elsewhere, or read again without it)."

    def request(self, item_id):
        """A DICTATED REQUEST as it stands NOW: what the Assistant's run of it is told, and what that run's cards are held to
        (assistant/workflows.py `observe`, kind `memo`). The request in one sentence, the exact words it rests on, the day it
        was dictated and the memo's own lines around it. Raises ValueError, in words the instructor can read, when it is no
        longer a request: the recording is not filed as a memo now, its latest reading does not hold the item, or the
        instructor dismissed it. Only `memo` (the recording, the item, the quote) is its identity."""
        item_id = str(item_id)
        with connect(self.path) as db:
            found = db.execute("SELECT recording_id FROM recording_items WHERE id=?", (item_id,)).fetchone()
            if not found:
                raise ValueError(self.GONE)
            row, links = self.record._one(db, found["recording_id"]), self.record._links(db, found["recording_id"])
            where = self._where(db, row, links)
            run = db.execute(f"{CURRENT} AND g.recording_id=?", (row["id"],)).fetchone() if where["meeting"] == "memo" else None
            read = db.execute("SELECT * FROM recording_item_readings WHERE reading_id=? AND item_id=?", (run["id"], item_id)).fetchone() if run else None
            if not read or read["kind"] != "todo":
                raise ValueError(self.GONE)
            last = db.execute("SELECT event FROM recording_item_events WHERE item_id=? AND event IN ('done','dismissed','reopened') ORDER BY rowid DESC LIMIT 1",
                              (item_id,)).fetchone()
            if last and last["event"] == "dismissed":
                raise ValueError("RECORDINGS: you dismissed this request on its memo.")
            # What Faculty asked BY ITSELF stands only while it is sure the words are the instructor's (a voice named as
            # somebody else since, a transcript Plaud changed): asked by their own press, it is their word and stands.
            asked = memos.runs(db, [item_id]).get(item_id)
            if asked and asked["auto"] and asked["state"] != "stale" and not self._voice(db, row["id"])[0]:
                raise ValueError(memos.NOT_SURE)
            lines = db.execute("SELECT position, start_ms, text FROM recording_lines WHERE file_id=? AND position>=? ORDER BY position LIMIT 60",
                               (run["transcript_file_id"], max(0, read["line"] - 8))).fetchall()
        said, size = [], 0
        for line in lines:
            text = f"{reading.clock(line['start_ms'])} {line['text']}"
            size += len(text) + 1
            if size > memos.SAID:
                break
            said.append(text)
        day = where["recorded"]
        return {"term_id": None, "title": f"Memo of {day_label(day)} · {read['sentence'][:120]}", "page": f"#recordings/{row['id']}?t={read['start_ms']}",
                "needs_review": True, "memo": {"recording_id": row["id"], "item_id": item_id, "quote": read["quote"]},
                "request": read["sentence"], "dictated": {"date": day.isoformat(), "weekday": f"{day:%A}", "began": where["began"]}, "said": said}

    def where_asked(self, item_id):
        """The page of the memo a dictated request was said in, at its words (for the Assistant's own list of its runs)."""
        with connect(self.path) as db:
            found = db.execute("""SELECT i.recording_id, MAX(x.start_ms) at FROM recording_items i
                LEFT JOIN recording_item_readings x ON x.item_id=i.id WHERE i.id=? GROUP BY i.recording_id""", (str(item_id),)).fetchone()
        return f"#recordings/{found['recording_id']}?t={found['at'] or 0}" if found else ""

    def _run_cards(self, run_id):
        """The cards of one dictated request's run, as the Assistant's own page shows them (in the order they were made)."""
        if not run_id or self.assistant is None:
            return []
        with connect(self.path) as db:
            return self.assistant.workflows.teaching(db, run_id)

    def _memo_sync(self, recording_id):
        """After a memo is read, after a recording is filed elsewhere, and after a request is dismissed or opened again:
        (1) a run whose request is no longer one is WITHDRAWN, its waiting cards with it (what was approved stands);
        (2) each open request of a memo that has no run yet is put to the Assistant, ONCE, when Faculty is sure the words
        are the instructor's (recordings/memos.py `voice`), the memo's first ASKS requests only. Anything else waits for
        the instructor's own press (`ask_assistant`). Nothing here asks a model: the run is queued for the web process."""
        if self.assistant is None:
            return
        workflows = self.assistant.workflows
        with connect(self.path) as db:
            row, links = self.record._one(db, recording_id), self.record._links(db, recording_id)
            dictated = self._where(db, row, links)["meeting"] == "memo"
            held = memos.runs(db, [item["id"] for item in db.execute("SELECT id FROM recording_items WHERE recording_id=?", (row["id"],))])
        for item_id, run in held.items():
            if run["state"] == "stale":
                continue
            try:
                self.request(item_id)
            except (ValueError, NotFound) as gone:
                workflows.dictated_gone(item_id, str(gone))
        if not dictated:
            return
        view = self.page(recording_id)
        if not (view.get("memo") or {}).get("sure"):
            return   # NOT SURE the words are the instructor's: nothing is asked by itself, and the page says why
        requests = [item for item in view["items"] if item["kind"] == "todo"]
        for item in requests[:memos.ASKS]:
            if item["state"] == "open" and item["ask"]["state"] == "unsent":
                try:
                    workflows.dictated(item["id"], self.request(item["id"]), f"{memos.AUTO}{item['id']}")
                except Exception:  # noqa: BLE001 - offered on the page instead (Ask the Assistant)
                    LOG.warning("A dictated request could not be put to the Assistant", exc_info=True)

    def voice_named(self, recording_id):
        """A speaker of a recording was named: a memo whose one voice is the instructor's now has its requests put to the
        Assistant, as it would have had when it was read (the page told them to name themself)."""
        try:
            with self._pressing:
                self._memo_sync(recording_id)
        except Exception:  # noqa: BLE001 - the name stands; Ask the Assistant is on the page
            LOG.warning("A memo's requests could not be put to the Assistant after its speaker was named", exc_info=True)

    def ask_assistant(self, recording_id, item_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/items/<item>/ask`: the instructor's own press of Ask the Assistant on a request of a
        memo: the first ask where Faculty was not sure the words were theirs (or the request was past the memo's first
        few), or Ask again, which replaces the earlier answer (its cards that still wait are withdrawn) and may carry a
        sentence of theirs for the Assistant (`guidance`). Every change the run wants waits as an Approve card. A repeated
        request ID asks nothing again. The Assistant cannot use this route (`assistant/policy.py`: never)."""
        data = data if isinstance(data, dict) else {}
        request_id, guidance = valid_request_id(data.get("request_id")), data.get("guidance", "")
        if not isinstance(guidance, str) or len(guidance) > 2000:
            raise ValueError("Keep what you add for the Assistant under 2,000 characters.")
        if self.assistant is None:
            raise Conflict("The Assistant is not available, so nothing can be asked of it.")
        with self._pressing:
            with connect(self.path) as db:
                _, where, _, read = self._item(db, recording_id, item_id)
                if where["meeting"] != "memo" or read["kind"] != "todo":
                    raise ValueError("Only a request dictated in a memo is put to the Assistant.")
                last = db.execute("SELECT event FROM recording_item_events WHERE item_id=? AND event IN ('done','dismissed','reopened') ORDER BY rowid DESC LIMIT 1",
                                  (read["item_id"],)).fetchone()
                if last and last["event"] != "reopened":
                    raise Conflict("You settled this request. Open it again to ask the Assistant.")
            try:
                observation = self.request(read["item_id"])
            except ValueError as gone:
                raise Conflict(str(gone)) from None
            self.assistant.workflows.dictated(read["item_id"], observation, f"memo:{request_id}", instruction=guidance, actor=actor, again=True)
        return self.record.recording(recording_id)

    # -- Rulings held for an assessment (GRADING "RULINGS SAID IN A RECORDING") --------------------------------------------------
    @staticmethod
    def _plan(db, assignment):
        """A graded assignment as a ruling is offered on it: {id, assignment_id, term_id, definition_id, title, certified}."""
        from grading import progress
        row = db.execute("""SELECT a.id, e.assignment_id, e.term_id, t.definition_id, e.title FROM frq_assignments a
            JOIN exam_events e ON e.id=a.event_id JOIN course_terms t ON t.id=e.term_id WHERE a.id=?""", (str(assignment or ""),)).fetchone()
        if not row:
            raise NotFound("This graded assignment no longer exists.")
        return {**dict(row), "certified": progress.latest_certification(db, row["id"]) is not None}

    @staticmethod
    def _names(db, plan):
        """What a ruling held by a name is matched against for one plan: its term's assessments ({id: title}) and the words of
        the course's code, which name nothing."""
        code = db.execute("SELECT code FROM course_terms WHERE id=?", (plan["term_id"],)).fetchone()
        return Readings._assessments(db, plan["term_id"], plan["definition_id"]), reading.name_words(code["code"] if code else "")

    @staticmethod
    def _of(plan, about, titles, apart):
        """Whether what was said (`about`) is of this plan's assessment: listed for it by the reading, or HELD BY NAME under a
        name that is this assessment among its term's (`reading.named`)."""
        if about.get("assessment"):
            return about["assessment"] == plan["assignment_id"]
        return about.get("what") in ("assessment", "rubric") and plan["assignment_id"] in reading.named(about.get("words"), titles, apart)

    def rulings(self, assignment):
        """`GET /api/recordings/rulings?assignment=<graded assignment>`: what recorded meetings said of how this assessment is
        graded, for its rubric's finalization: each ruling of the latest reading of a recording filed under the assessment's
        term or course, with its quote, who said it and where to hear it, the question it named, and whether the instructor
        accepted or declined it; and `not_final` when the latest thing said of this assessment's rubric is that it is NOT final
        (the warning where its rubric is imported). On a certified plan nothing is offered.

        HELD BY NAME: a ruling of an assessment the reading had no listing for carries the name the meeting called it by, and is
        offered here when that name is THIS assessment among its term's (`reading.named`: by code, at each ask, so also on an
        assessment made after the meeting); `named` then says the name, so the window shows why it is here."""
        with connect(self.path) as db:
            plan = self._plan(db, assignment)
            titles, apart = self._names(db, plan)
            rows = [dict(row) for row in db.execute(f"""SELECT x.*, g.recording_id, v.started_at, v.speakers_json,
                    (SELECT n.speaker FROM recording_lines n WHERE n.file_id=g.transcript_file_id AND n.speaker_key=x.speaker_key LIMIT 1) plaud_name
                FROM recording_item_readings x
                JOIN ({CURRENT}) g ON g.id=x.reading_id JOIN ({LATEST}) v ON v.id=g.recording_id
                WHERE (json_extract(x.about_json,'$.assessment')=?
                       OR (COALESCE(json_extract(x.about_json,'$.assessment'),'')='' AND COALESCE(json_extract(x.about_json,'$.words'),'')<>''
                           AND json_extract(x.about_json,'$.what') IN ('assessment','rubric')))
                  AND (x.kind='ruling' OR json_extract(x.about_json,'$.what')='rubric')
                  AND EXISTS ({LINKS} AND l.recording_id=g.recording_id AND l.owner_key IN (?,?))
                ORDER BY v.started_at, x.line""", (plan["assignment_id"], f"term:{plan['term_id']}", f"course:{plan['definition_id']}"))]
            # One listed for this assessment is its own; one held by a name is here only when that name is this assessment.
            rows = [row for row in rows if self._of(plan, json.loads(row["about_json"] or "{}"), titles, apart)]
            ruled = {}
            for event in db.execute(f"""SELECT e.item_id, e.event FROM recording_item_events e WHERE e.event IN ('accepted','declined','reopened','dismissed','done')
                    AND e.item_id IN ({','.join('?' * len(rows)) or "''"}) ORDER BY e.rowid""", [row["item_id"] for row in rows]):
                ruled[event["item_id"]] = event["event"]

        def said(row):
            given = {key: name for key, name in json.loads(row["speakers_json"] or "{}").items() if isinstance(name, str) and name}
            about, day = json.loads(row["about_json"] or "{}"), filing.campus(parse(row["started_at"])).date()
            return {"id": row["item_id"], "recording_id": row["recording_id"], "sentence": row["sentence"], "quote": row["quote"],
                    "speaker": given.get(row["speaker_key"]) or row["plaud_name"] or "Unnamed speaker", "at": transcript.offset(row["start_ms"]),
                    "date": day.isoformat(), "day": day_label(day), "question": about.get("question", ""),
                    "named": "" if about.get("assessment") else str(about.get("words") or ""),
                    "href": f"#recordings/{row['recording_id']}?t={row['start_ms']}",
                    "state": {"accepted": "accepted", "done": "accepted", "declined": "declined", "dismissed": "declined"}.get(ruled.get(row["item_id"]), "waiting")}

        statements = [row for row in rows if json.loads(row["about_json"]).get("what") == "rubric"]
        latest = statements[-1] if statements else None
        return {"assignment": plan["id"], "title": plan["title"], "certified": plan["certified"],
                "rulings": [] if plan["certified"] else [said(row) for row in rows if row["kind"] == "ruling"],
                "not_final": said(latest) if latest and not json.loads(latest["about_json"]).get("final") and not plan["certified"] else None}
