"""Recordings, step 3: the reading (docs/decisions/recordings.md, RECORDINGS "The reading (step 3)", "What an item may become")
offline, against the stand-in of the Plaud intake (tests/fake_intake.py) and a SCRIPTED engine in the Assistant's place: a filed
meeting is read on its own, only what its quote check keeps is shown, what Faculty's own records say is found by code, a date
becomes an Approve card and nothing else, a standing rule is kept only by the instructor's own Remember, and a ruling waits for
its assessment's rubric. No model, no Plaud and no Google is reached; the people and their words are invented."""
import json
import sqlite3
import unittest
import uuid
from contextlib import contextmanager
from datetime import date
from unittest import mock

from test_assistant_memory import answering
from test_recordings import ALICE, HEADERS, TUE, WED, campus
from test_recordings_page import OUTLINE, SUMMARY, PageBase

from assistant import policy
from grading.providers.base import ProviderError
from recordings import checks, reading, readings

THU, NEXT_TUE = "2026-11-12", "2026-11-17"


def said(start, speaker, label, words):
    return {"start_time": start * 1000, "end_time": (start + 4) * 1000, "speaker": speaker, "original_speaker": label,
            "segment_uid": f"u{start}", "content": words}


# A coordinator's meeting, as Plaud would give it (the shape of its own transcripts; nobody here is real).
MEETING = [
    said(0, "Chris", "Speaker 1", "Is this thing recording? Okay, it is on."),
    said(4, "Avery Quill", "Speaker 2", "Let us start. Seating charts for Exam 3 have to be rebuilt after the drop deadline."),
    said(8, "Chris", "Speaker 1", "How many of mine are at the testing center for Exam 3?"),
    said(12, "Avery Quill", "Speaker 2", "You tell me, I do not have that list."),
    said(16, "Avery Quill", "Speaker 2", "We will not meet next Tuesday, that meeting is cancelled."),
    said(20, "Avery Quill", "Speaker 2", "The grading meeting is Thursday at three in Blocker 220."),
    said(24, "Avery Quill", "Speaker 2", "On question five, give two points for the correct derivative"),
    said(28, "Avery Quill", "Speaker 2", "even when the sign is wrong."),
    said(32, "Avery Quill", "Speaker 2", "From now on, late work is never accepted after solutions are posted."),
    said(36, "Avery Quill", "Speaker 2", "I will send the answer key by Friday."),
    said(40, "Avery Quill", "Speaker 2", "The Exam 3 rubric is not final yet, I am still changing it."),
    said(44, "Avery Quill", "Speaker 2", "Everyone publish the Exam 3 review module on Canvas."),
    said(48, "Avery Quill", "Speaker 2", "And nobody drops a quiz score in the gradebook this term."),
]


def item(kind, sentence, quote, line, **more):
    about = {"what": "none", "assessment": "", "question": "", "words": "", "final": False, **more.pop("about", {})}
    return {"kind": kind, "sentence": sentence, "quote": quote, "line": line, "for": "you", "date": "", "time": "", "date_words": "",
            "happening": True, "about": about, **more}


def answer(exam="a1"):
    """What the scripted engine answers for MEETING: twelve items, of which the quote check keeps ten."""
    return {"begins_at_line": 1, "items": [
        item("todo", "Rebuild the Exam 3 seating charts after the drop deadline.",
             "Seating charts for Exam 3 have to be rebuilt after the drop deadline.", 1, about={"what": "seating", "assessment": exam}),
        item("answer", "The coordinator does not have the list of your testing center students for Exam 3.",
             "You tell me, I do not have that list.", 3, about={"what": "testing_center", "assessment": exam}),
        item("date", "Next Tuesday's meeting is cancelled.", "We will not meet next Tuesday, that meeting is cancelled.", 4,
             date=NEXT_TUE, date_words="next Tuesday", happening=False, about={"what": "calendar", "words": "All instructors meeting"}),
        item("date", "The Exam 3 grading meeting is Thursday at 3 pm in Blocker 220.", "The grading meeting is Thursday at three in Blocker 220.", 5,
             date=THU, time="15:00", date_words="Thursday at three", about={"what": "calendar", "words": "Exam 3 grading meeting"}),
        # A quote may run on into the next line of the same voice.
        item("ruling", "On question 5, the correct derivative earns two points even when its sign is wrong.",
             "give two points for the correct derivative even when the sign is wrong.", 6, **{"for": "others"},
             about={"what": "assessment", "assessment": exam, "question": "5"}),
        item("policy", "Late work is never accepted after solutions are posted.",
             "From now on, late work is never accepted after solutions are posted.", 8),
        item("waiting", "The coordinator will send the answer key by Friday.", "I will send the answer key by Friday.", 9,
             **{"for": "coordinator"}, date="2026-11-13", date_words="by Friday"),
        item("todo", "The Exam 3 rubric is still changing.", "The Exam 3 rubric is not final yet, I am still changing it.", 10,
             about={"what": "rubric", "assessment": exam, "final": False}),
        item("todo", "Publish the Exam 3 review module on Canvas.", "Everyone publish the Exam 3 review module on Canvas.", 11,
             about={"what": "canvas", "words": "Exam 3 review"}),
        item("todo", "Drop no quiz score in the gradebook this term.", "And nobody drops a quiz score in the gradebook this term.", 12,
             about={"what": "gradebook"}),
        # What the quote check throws away: words that were never said, and talk before the meeting began.
        item("todo", "Bring donuts on Friday.", "Somebody should bring donuts on Friday.", 5),
        item("todo", "Check that the recorder is on.", "Is this thing recording? Okay, it is on.", 0),
    ]}


class Engine:
    """The Assistant's engine, scripted: it answers what a test says and remembers what it was asked."""

    def __init__(self):
        self.asked, self.error, self.answer = [], None, None

    def submit(self, request):
        self.asked.append(request)
        if self.error:
            raise self.error
        exam = next((entry["id"] for entry in request["payload"]["facts"]["assessments"] if entry["title"] == "Exam 3"), "none")
        output = self.answer(exam) if callable(self.answer) else self.answer
        return {"state": "completed", "result": {"output": output, "metadata": {"requested_model": "scripted-model"}}}


class ReadingBase(PageBase):
    connection = {"name": "scripted", "kind": "hosted", "model": "scripted-model"}

    def setUp(self):
        super().setUp()
        self.engine = Engine()
        self.engine.answer = answer
        # The Assistant's connection as Compute settings would name it, and the one seam to any vendor (grading/cloud.py).
        self.record.reading.connection = lambda: self.connection
        self.limits = []   # the time limit each request was given
        patch = mock.patch.object(self.app.extensions["frq_grading"], "cloud_provider",
                                  side_effect=lambda endpoint, timeout: self.limits.append(timeout) or self.engine)
        patch.start()
        self.addCleanup(patch.stop)
        with self.db() as db:
            db.execute("UPDATE settings SET teacher_name='Chris Example' WHERE id=1")
        self.exams = self.app.extensions["absences"].exams
        self.assignment = self.app.extensions["documents"].create_assignment(self.definition, "exam-3", "Exam 3")["id"]
        self.exam = self.exams.create({"term_id": self.term, "assignment_id": self.assignment, "section_ids": list(self.sections.values())})
        self.exams.save_sitting(self.exam["id"], {"version": self.exam["version"], "label": "Exam 3", "kind": "main", "date": "2026-11-19"})

    def read(self, plaud_id="rec-meeting", **more):
        """The meeting filed under the term, kept, and read in the same round."""
        return self.kept(plaud_id, **{"transcript": MEETING, **more})

    def items(self, recording):
        return {entry["sentence"]: entry for entry in self.one(recording)["items"]}

    def post(self, path, **body):
        return self.client.post(f"/api/recordings/{path}", headers=HEADERS, json={"request_id": str(uuid.uuid4()), **body})

    def runs(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM recording_readings ORDER BY run")]

    def cards(self):
        return self.client.get("/api/assistant/actions").json["pending"]


class ReadingTests(ReadingBase):
    def test_a_filed_meeting_is_read_on_its_own_and_only_what_its_quote_holds_is_kept(self):
        recording = self.read()
        page = self.one(recording)
        self.assertEqual({key: page["reading"][key] for key in ("state", "run", "engine", "begins_line", "you", "again")},
                         {"state": "read", "run": 1, "engine": "scripted · scripted-model", "begins_line": 1, "you": "Chris", "again": False})
        self.assertEqual(len(page["items"]), 10)
        ruling = self.items(recording)["On question 5, the correct derivative earns two points even when its sign is wrong."]
        # Who spoke and when come from the transcript, never from the model; the quote ran on into the next line.
        self.assertEqual((ruling["kind"], ruling["speaker"], ruling["line"], ruling["start_ms"], ruling["end_ms"], ruling["at"], ruling["for"]),
                         ("ruling", "Avery Quill", 6, 24000, 32000, "0:24", "others"))
        self.assertEqual((ruling["about"], ruling["assessment"], ruling["group"]),
                         ({"what": "assessment", "assessment": self.assignment, "question": "5"}, "Exam 3", "rulings"))
        # What was dropped is recorded with the reason, and is no item.
        self.assertEqual([(dropped["sentence"], dropped["reason"]) for dropped in page["reading"]["dropped"]],
                         [("Bring donuts on Friday.", "its quote is not in the transcript at that line"),
                          ("Check that the recorder is on.", "said before the meeting began")])
        # ONE request: the instructions by name, the facts and the transcript's lines. Never the audio, never Plaud's summary.
        (request,) = self.engine.asked
        self.assertEqual((request["prompt"], request["schema"]), (reading.PROMPT, reading.SCHEMA))
        # One answer now: never a batch job collected later, and a hosted model is given no time limit (GRADING, 2026-09-23).
        self.assertEqual((request["durable"], self.limits), (False, [None]))
        facts, sent = request["payload"]["facts"], request["payload"]["transcript"]
        self.assertEqual(facts["recorded"], {"date": TUE, "weekday": "Tuesday", "began": "10:02"})
        self.assertEqual((facts["instructor"], facts["coordinator"], facts["course"], facts["term"]),
                         ({"name": "Chris Example", "speaker": "Chris"}, "", "MATH 140", "Fall 2026"))
        self.assertEqual(facts["assessments"], [{"id": "a1", "title": "Exam 3", "given": "2026-11-19"}])
        self.assertEqual(([section["number"] for section in facts["sections"]], facts["canvas"]), ([507, 508], {}))   # no Canvas copy yet
        self.assertIn({"date": TUE, "time": "10:00", "title": "MATH 140 - all instructors"}, facts["calendar"])
        self.assertEqual(sent.splitlines()[1], "[1] 0:04 Avery Quill: Let us start. Seating charts for Exam 3 have to be rebuilt after the drop deadline.")
        self.assertNotIn("Calculators are not allowed", json.dumps(request))   # Plaud's summary is never read for this
        self.assertTrue({command for command, _ in self.fake.asked()} <= {"list", "show", "audio", "status", "check-now"})   # nothing was sent anywhere
        # The run is a record of what was asked: the checksums of the instructions and of what was sent.
        (run,) = self.runs()
        self.assertEqual((run["status"], run["version"], run["actor"], run["filed_as"], len(run["prompt_sha256"]), run["input_sha256"]),
                         ("read", "reading-v3", "automatic", f"term:{self.term}", 64, reading.checksum(request["payload"])))
        # A later round asks nothing again: the recording is read.
        self.round(later=700)
        self.assertEqual(len(self.engine.asked), 1)

    def test_no_student_and_no_other_google_entry_is_among_the_facts(self):
        self.appointment(ALICE, TUE, "14:00", "14:30")
        self.google.add("Dentist", WED, "09:00", "10:00")
        self.google.add("MATH 140 common exam proctoring", THU, "19:00", "21:00")
        self.read()
        calendar = self.engine.asked[0]["payload"]["facts"]["calendar"]
        self.assertEqual([entry["title"] for entry in calendar if entry["date"] in (TUE, WED, THU)],
                         ["MATH 140 - all instructors", "MATH 140 common exam proctoring"])
        self.assertNotIn("Alice", json.dumps(self.engine.asked[0]["payload"]["facts"]))

    def test_a_training_is_kept_for_reference_and_not_read(self):
        # Step 4 reads a student's meeting (tests/test_recordings_student_meetings.py), step 5 a class
        # (tests/test_recordings_classes.py) and step 6 a memo (tests/test_recordings_memos.py). A TRAINING (step 6) is
        # filed under its course for reference and is never read for items.
        self.fake.add("rec-student", campus(WED, "13:00"), transcript=MEETING)
        self.round()
        self.assertEqual(self.file("rec-student", kind="training", definition_id=self.definition).status_code, 200)
        self.round()
        self.round(later=30)
        self.assertEqual(self.engine.asked, [])
        page = self.one(self.item("rec-student")["id"])
        self.assertEqual((page["reading"]["state"], page["reading"]["detail"], page["items"]), ("not_read", readings.TRAINING, []))
        # Filed under the course as a meeting instead, it is read.
        self.assertEqual(self.file("rec-student", kind="course", definition_id=self.definition).status_code, 200)
        self.round()
        self.assertEqual(len(self.engine.asked), 1)
        self.assertEqual(self.one(self.item("rec-student")["id"])["reading"]["state"], "read")

    def test_read_again_is_a_new_run_and_what_was_answered_stays_answered(self):
        recording = self.read()
        policy_item = self.items(recording)["Late work is never accepted after solutions are posted."]
        self.assertEqual(self.post(f"{recording}/items/{policy_item['id']}", state="dismissed").status_code, 200)
        # The next reading words one item differently (same quote) and no longer holds another.
        self.engine.answer = lambda exam: {"begins_at_line": 1, "items": [
            {**found, "sentence": "No late work once solutions are out."} if found["kind"] == "policy" else found
            for found in answer(exam)["items"] if found["kind"] != "waiting"]}
        asked = self.post(f"{recording}/read", request_id="read-1")
        self.assertEqual((asked.status_code, asked.json["reading"]["again"]), (200, True))
        self.assertEqual(self.post(f"{recording}/read", request_id="read-1").status_code, 200)   # asked once
        self.record.run_due()   # the worker, within seconds: no round is due, and Plaud is asked nothing
        self.assertEqual(len(self.engine.asked), 2)
        page = self.one(recording)
        self.assertEqual((page["reading"]["run"], page["reading"]["again"], len(page["items"])), (2, False, 9))
        again = self.items(recording)["No late work once solutions are out."]
        self.assertEqual((again["id"], again["state"], again["group"]), (policy_item["id"], "dismissed", "done"))
        self.assertEqual([(run["run"], run["request_id"], run["actor"]) for run in self.runs()], [(1, "", "automatic"), (2, "read-1", "workspace")])
        self.record.run_due()
        self.assertEqual(len(self.engine.asked), 2)   # nothing more is wanted
        # Opened again, it needs the instructor again; a request ID settles one item once.
        self.assertEqual(self.post(f"{recording}/items/{again['id']}", state="reopened", request_id="open-1").status_code, 200)
        self.assertEqual(self.items(recording)["No late work once solutions are out."]["group"], "you")
        other = self.items(recording)["Publish the Exam 3 review module on Canvas."]["id"]
        self.assertEqual(self.post(f"{recording}/items/{other}", state="done", request_id="open-1").status_code, 409)
        self.assertEqual(self.post(f"{recording}/items/{again['id']}", state="graded").status_code, 400)
        self.assertEqual(self.post(f"{recording}/items/nothing", state="done").status_code, 404)

    def test_a_transcript_plaud_changed_is_read_again_by_itself(self):
        recording = self.read()
        self.fake.recordings["rec-meeting"]["transcript"] = [*MEETING, said(52, "Avery Quill", "Speaker 2", "That is everything for today.")]
        self.round(later=3700)   # the first read again, about an hour after it was kept
        self.assertEqual([(run["run"], run["status"]) for run in self.runs()], [(1, "read"), (2, "read")])
        self.assertEqual(self.runs()[0]["transcript_file_id"] == self.runs()[1]["transcript_file_id"], False)
        page = self.one(recording)
        self.assertEqual((len(page["items"]), page["reading"]["changed"], page["reading"]["again"]), (10, False, False))

    def test_a_changed_transcript_that_cannot_be_read_says_why_and_leaves_read_again_to_the_instructor(self):
        recording = self.read()
        self.connection = None   # the Assistant is turned off, and then Plaud changes the transcript
        self.fake.recordings["rec-meeting"]["transcript"] = [*MEETING, said(52, "Avery Quill", "Speaker 2", "That is everything for today.")]
        self.round(later=3700)
        page = self.one(recording)
        # The earlier reading still shows; the page says why the new transcript is not read, and never that it is being read.
        self.assertEqual({key: page["reading"][key] for key in ("state", "run", "again", "changed", "detail", "readable")},
                         {"state": "read", "run": 1, "again": False, "changed": True, "detail": readings.OFF, "readable": True})
        self.assertEqual((len(page["items"]), len(self.engine.asked)), (10, 1))
        self.connection = ReadingBase.connection
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)   # theirs to press
        self.record.run_due()
        page = self.one(recording)
        self.assertEqual((page["reading"]["run"], page["reading"]["changed"], page["reading"]["detail"]), (2, False, ""))

    def test_read_again_asked_for_while_a_reading_is_being_made_is_made_next(self):
        recording = self.read()
        pressed = []

        def answering_slowly(exam):
            if not pressed:   # the instructor presses Read again while the engine is still writing
                pressed.append(self.post(f"{recording}/read", request_id="second").status_code)
            return answer(exam)

        self.engine.answer = answering_slowly
        self.assertEqual(self.post(f"{recording}/read", request_id="first").status_code, 200)
        self.record.run_due()
        self.assertEqual((pressed, len(self.engine.asked), self.record.reading.wanted()), ([200], 2, True))
        self.record.run_due()
        self.assertEqual((len(self.engine.asked), self.record.reading.wanted()), (3, False))
        self.assertEqual([(run["run"], run["request_id"]) for run in self.runs()], [(1, ""), (2, "first"), (3, "second")])

    def test_a_quote_must_begin_in_the_line_it_names(self):
        lines = [{"line": n, "start_ms": n * 1000, "end_ms": n * 1000 + 900, "speaker_key": "Speaker 2", "text": text}
                 for n, text in enumerate(["Hello and welcome everyone.", "Give two points for the derivative", "even when the sign is wrong."])]
        found = lambda quote, line: reading.checked({"begins_at_line": 0, "items": [item("ruling", "A ruling.", quote, line)]}, lines, {}, date(2026, 11, 10))
        _, kept, dropped = found("two points for the derivative even when the sign", 1)   # begins in line 1, runs into line 2
        self.assertEqual([(entry["line"], entry["line_end"], entry["start_ms"], entry["end_ms"]) for entry in kept], [(1, 2, 1000, 2900)])
        for quote, line in (("even when the sign is wrong.", 1),      # wholly in a later line: it would play the wrong moment
                            ("Give two points for the derivative", 0),  # said after the line it names
                            ("give two points for the derivative", 1),  # not word for word
                            ("two points for the sign", 1)):            # words joined from two places
            _, kept, dropped = found(quote, line)
            self.assertEqual((kept, [entry["reason"] for entry in dropped]), ([], ["its quote is not in the transcript at that line"]), quote)

    def test_an_engine_that_is_off_or_does_not_answer_makes_no_run_and_is_tried_again_less_often(self):
        self.connection = None   # no model is chosen for the Assistant
        recording = self.read()
        page = self.one(recording)
        self.assertEqual((page["reading"]["state"], page["reading"]["detail"], page["items"], self.engine.asked, self.runs()),
                         ("waiting", readings.OFF, [], [], []))
        self.connection = ReadingBase.connection
        self.engine.error = ProviderError("Endpoint unavailable or timed out")
        self.round(later=3700)
        self.assertEqual((len(self.engine.asked), self.runs()), (1, []))
        self.assertEqual(self.one(recording)["reading"]["detail"], "The Assistant's engine did not read it: Endpoint unavailable or timed out")
        self.round(later=300)   # not yet: it waits ten minutes after the first failure
        self.assertEqual(len(self.engine.asked), 1)
        for expected, later in ((2, 700), (3, 3700), (4, 6 * 3600 + 100), (5, 24 * 3600 + 100)):
            self.round(later=later)
            self.assertEqual(len(self.engine.asked), expected)
        self.round(later=3 * 24 * 3600)   # after five tries it waits for Read again
        self.assertEqual(len(self.engine.asked), 5)
        page = self.one(recording)
        self.assertEqual((page["reading"]["state"], page["reading"]["stopped"]), ("failed", True))
        self.engine.error = None
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual((self.one(recording)["reading"]["state"], len(self.runs())), ("read", 1))

    def test_an_answer_faculty_cannot_use_is_a_failed_run(self):
        self.engine.answer = "I could not find anything in this transcript."
        recording = self.read()
        (run,) = self.runs()
        self.assertEqual((run["status"], run["detail"]), ("failed", "The engine's answer was not a reading Faculty can use."))
        page = self.one(recording)
        self.assertEqual((page["reading"]["state"], page["reading"]["detail"], page["items"]), ("failed", run["detail"], []))
        # A model that wraps its answer in a sentence and a fence is still read.
        self.engine.answer = lambda exam: "Here it is:\n```json\n" + json.dumps(answer(exam)) + "\n```"
        self.round(later=700)
        self.assertEqual([(run["run"], run["status"]) for run in self.runs()], [(1, "failed"), (2, "read")])
        self.assertEqual(len(self.one(recording)["items"]), 10)

    def test_a_reading_that_breaks_in_faculty_is_said_and_tried_again_less_often(self):
        with mock.patch.object(reading, "checked", side_effect=RuntimeError("broken")), self.assertLogs("recordings.readings", level="ERROR"):
            recording = self.read()
            self.round(later=300)   # not asked again every round
        self.assertEqual((len(self.engine.asked), self.runs()), (1, []))
        page = self.one(recording)
        self.assertEqual((page["reading"]["state"], page["reading"]["detail"]), ("waiting", "Faculty could not keep this reading (RuntimeError)."))
        self.round(later=700)
        self.assertEqual((len(self.engine.asked), self.one(recording)["reading"]["state"]), (2, "read"))

    def test_a_recording_with_no_readable_transcript_cannot_be_asked_for(self):
        self.meeting("rec-quiet", transcript=None)
        self.round()
        recording = self.item("rec-quiet")["id"]
        self.assertEqual(self.post(f"{recording}/read").status_code, 409)
        self.assertEqual((self.one(recording)["reading"]["state"], self.engine.asked), ("none", []))

    def test_runs_items_and_what_became_of_them_are_append_only(self):
        self.read()
        with self.db() as db:
            for statement in ("UPDATE recording_readings SET status='failed'", "DELETE FROM recording_readings",
                              "UPDATE recording_item_readings SET sentence='x'", "DELETE FROM recording_item_readings",
                              "UPDATE recording_items SET item_key='x'", "DELETE FROM recording_items"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)


class FoundTests(ReadingBase):
    """CHECKED AGAINST FACULTY: by code, from the owning tables, recomputed at each read."""

    def test_an_empty_catalog_answers_plainly_from_the_real_tables(self):
        recording = self.read()
        found = {sentence: (entry["found"] or {}) for sentence, entry in self.items(recording).items()}
        self.assertEqual(found["Rebuild the Exam 3 seating charts after the drop deadline."],
                         {"state": "open", "text": "Faculty has no seating chart built for Exam 3 yet.", "href": "#seating"})
        self.assertEqual(found["The coordinator does not have the list of your testing center students for Exam 3."]["text"],
                         "Faculty has no testing center booking for Exam 3.")
        self.assertEqual(found["Publish the Exam 3 review module on Canvas."]["text"], "Faculty has no copy of this term's Canvas modules and pages to look in.")
        self.assertEqual(found["Drop no quiz score in the gradebook this term."]["text"], "The gradebook has no categories yet, so no drops are set.")
        self.assertEqual(found["Late work is never accepted after solutions are posted."], {})

    @contextmanager
    def records(self):
        """The tables the checks read, with their real columns (copied from the catalog's own definitions) and no rows: a
        check's arithmetic is tried on rows a test writes, without the modules that own them."""
        names = ("seating_charts", "roster_drops", "roster_dropped", "course_sections", "gradebook_setups", "canvas_content_revisions",
                 "dr_exam_bookings", "dr_exam_revisions", "dr_exam_assignment_links")
        copy = sqlite3.connect(":memory:")
        copy.row_factory = sqlite3.Row
        with self.db() as db:
            for name in names:
                copy.execute(db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0])
        try:
            yield copy
        finally:
            copy.close()

    def test_seating_charts_are_done_only_when_built_after_the_last_drop(self):
        with self.records() as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(course_sections)")]
            db.execute(f"INSERT INTO course_sections({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                       ["s7" if column == "id" else "t1" if column == "term_id" else 507 if column == "number" else "" for column in columns])
            chart = "INSERT INTO seating_charts(id, assignment_id, term_id, room_id, generated_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)"
            db.execute(chart, ("c1", "x3", "t1", "r1", "2026-11-09T15:00:00Z", "2026-11-01T00:00:00Z", "2026-11-09T15:00:00Z"))
            self.assertEqual(checks.seating(db, "t1", "x3", "Exam 3")["text"],
                             "Already done: 1 seating chart for Exam 3, the last built Mon, Nov 9; Faculty has no drop on record.")
            db.execute("INSERT INTO roster_drops VALUES (?,?,?,?,?,?,?,?,?)", ("d1", "s7", "e1", "drop", "roster", "", "q1", "workspace", "2026-11-10T16:00:00Z"))
            self.assertEqual(checks.seating(db, "t1", "x3", "Exam 3"),
                             {"state": "open", "href": "#seating", "text": "1 of 1 seating chart for Exam 3 was built before the last drop Faculty has (Tue, Nov 10)."})
            db.execute(chart, ("c2", "x3", "t1", "r2", "2026-11-11T15:00:00Z", "2026-11-01T00:00:00Z", "2026-11-11T15:00:00Z"))
            self.assertEqual(checks.seating(db, "t1", "x3", "Exam 3")["state"], "open")   # one of the two is still older than the drop
            db.execute("UPDATE seating_charts SET generated_at='2026-11-11T16:00:00Z' WHERE id='c1'")
            self.assertEqual(checks.seating(db, "t1", "x3", "Exam 3")["text"],
                             "Already done: 2 seating charts for Exam 3, the last built Wed, Nov 11, after the last drop Faculty has (Tue, Nov 10).")
            # A testing center chart is not a classroom's.
            db.execute("UPDATE seating_charts SET dr_exam_key='dr' WHERE id IN ('c1','c2')")
            self.assertEqual(checks.seating(db, "t1", "x3", "Exam 3")["state"], "open")

    def test_the_testing_center_is_a_ready_number_by_day(self):
        with self.records() as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(course_sections)")]
            db.execute(f"INSERT INTO course_sections({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                       ["s7" if column == "id" else "t1" if column == "term_id" else 507 if column == "number" else "" for column in columns])
            for number, day in enumerate(["2026-11-19"] * 14 + ["2026-11-20", "2026-11-23"]):
                db.execute("INSERT INTO dr_exam_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                           (f"v{number}", "s7", f"e{number}", "exam-3", "job", number, json.dumps({"date": day}), "0" * 64, "2026-11-01T00:00:00Z"))
                db.execute("INSERT INTO dr_exam_bookings VALUES (?,?,?,?,?)", (f"b{number}", "s7", f"e{number}", "exam-3", f"v{number}"))
                db.execute("INSERT INTO dr_exam_assignment_links VALUES (?,?,?,?,?)", (f"b{number}", "x3", "matched", "", ""))
            found = checks.testing_center(db, "t1", "x3", "Exam 3", "MATH 140")
            self.assertEqual((found["state"], found["text"]),
                             ("number", "Testing center bookings for Exam 3: Thu, Nov 19: 14 · Fri, Nov 20: 1 · Mon, Nov 23: 1."))
            self.assertIn("exam=Exam%203", found["href"])
            self.assertEqual(checks.testing_center(db, "t1", "other", "Exam 2", "MATH 140")["state"], "open")

    def test_the_canvas_copy_and_the_gradebook_say_what_they_hold(self):
        with self.records() as db:
            content = {"modules": [{"name": "Exam 3 Review", "published": True}, {"name": "Exam 3 Review (old)", "published": False}],
                       "pages": [{"title": "Syllabus", "published": True}], "assignments": [{"name": "Quiz 9", "published": False}]}
            db.execute("INSERT INTO canvas_content_revisions VALUES (?,?,?,?,?,?,?,?,?)", ("c1", "t1", 1, json.dumps(content), "", "q", "", "w", ""))
            self.assertEqual(checks.canvas(db, "t1", "exam 3 review"),
                             {"state": "done", "href": "", "text": "Already done: the module “Exam 3 Review” is published in Faculty's Canvas copy (1 other match)."})
            self.assertEqual(checks.canvas(db, "t1", "Quiz 9")["text"], "The assignment “Quiz 9” is in Faculty's Canvas copy, not published.")
            self.assertEqual(checks.canvas(db, "t1", "final review")["text"], "Nothing in Faculty's Canvas copy is called “final review”.")
            # No name holds every word: the closest names are said, and nothing is claimed (first live reading, 2026-11-13).
            self.assertEqual(checks.canvas(db, "t1", "Exam 3 information"),
                             {"state": "info", "href": "", "text": "Nothing in Faculty's Canvas copy is called “Exam 3 information”. Closest: "
                                                                   "the module “Exam 3 Review” (published), the module “Exam 3 Review (old)” (not published)."})
            # The names a reading is told, so an item can name the thing as the copy does.
            self.assertEqual(checks.canvas_names(db, "t1"), {"modules": ["Exam 3 Review", "Exam 3 Review (old)"], "pages": ["Syllabus"], "assignments": ["Quiz 9"]})
            self.assertEqual(checks.canvas_names(db, "another term"), {})
            setup = {"categories": [{"name": "Quizzes", "drop_lowest": 0}, {"name": "Homework", "drop_lowest": 2}]}
            db.execute("INSERT INTO gradebook_setups VALUES (?,?,?,?,?,?,?,?,?)", ("g1", "t1", 1, json.dumps(setup), "0" * 64, "q", "0" * 64, "w", ""))
            self.assertEqual(checks.gradebook(db, "t1")["text"], "The gradebook drops the lowest 2 in Homework.")
            setup["categories"][1]["drop_lowest"] = 0
            db.execute("INSERT INTO gradebook_setups VALUES (?,?,?,?,?,?,?,?,?)", ("g2", "t1", 2, json.dumps(setup), "0" * 64, "q2", "0" * 64, "w", ""))
            self.assertEqual(checks.gradebook(db, "t1"), {"state": "info", "href": "#gradebook/t1", "text": "No drops are set in the gradebook."})

    def test_a_cancelled_meeting_lists_the_google_entries_and_changes_none(self):
        self.google.add("MATH 140 - all instructors", NEXT_TUE, "10:00", "11:15")
        self.google.add("Dentist", NEXT_TUE, "08:00", "09:00")
        recording = self.read()
        before = [dict(row) for row in self.google.rows]
        cancelled = self.items(recording)["Next Tuesday's meeting is cancelled."]
        self.assertEqual((cancelled["found"]["state"], cancelled["found"]["text"], cancelled["group"], cancelled["when"]),
                         ("listed", "Still on your calendar (1 entry): Tue, Nov 17 “MATH 140 - all instructors” 10:00 AM. "
                                    "Faculty does not change your Google entries: removing them is yours.", "you", "Tue, Nov 17"))
        self.assertEqual(self.google.rows, before)
        self.assertNotIn("All instructors meeting", json.dumps(self.cards()))   # nothing is proposed for a meeting called off
        # Once the instructor has removed it themself, nothing is left to do.
        self.google.rows = [row for row in self.google.rows if row["date"] != NEXT_TUE or row["title"] == "Dentist"]
        cancelled = self.items(recording)["Next Tuesday's meeting is cancelled."]
        self.assertEqual((cancelled["found"]["state"], cancelled["found"]["text"], cancelled["group"]),
                         ("done", "Nothing to remove: no meeting of this course is on your calendar on Tue, Nov 17.", "done"))

    def test_a_class_meeting_and_office_hours_are_never_the_meeting_that_was_called_off(self):
        # First live reading, 2026-11-13: a called-off meeting listed that day's class meetings and office hours.
        self.schedule({"kind": "class", "weekdays": [1], "start": "08:00", "end": "08:50", "room": "HELD 107", "section_id": self.sections[507]},
                      {"kind": "office_hours", "weekdays": [1], "start": "15:00", "end": "17:00", "room": "BLOC 309", "seats_makeup": False})
        # Second live reading, 2026-11-13: nor is a class event made by hand for one section (a redefined day's four classes).
        self.event("MATH 140 · Section 508", NEXT_TUE, "09:10", "10:00", scope="section", definition_id=self.definition, term_id=self.term,
                   section_id=self.sections[508])
        self.assertTrue(checks.class_meeting({"source": "custom", "section_id": "s1", "title": "MATH 140 · Section 508"}))
        self.assertFalse(checks.class_meeting({"source": "custom", "section_id": "s1", "title": "MATH 140 · Section 508 review session"}))
        self.assertFalse(checks.class_meeting({"source": "custom", "section_id": None, "title": "MATH 140 · Section 508"}))
        self.assertFalse(checks.class_meeting({"source": "google", "section_id": "s1", "title": "MATH 140 · Section 508"}))
        self.google.add("MATH 140 - all instructors", NEXT_TUE, "10:00", "11:15")
        recording = self.read()
        cancelled = self.items(recording)["Next Tuesday's meeting is cancelled."]
        self.assertEqual((cancelled["found"]["state"], cancelled["found"]["entries"]), ("listed", 1))
        self.assertNotIn("Office hours", cancelled["found"]["text"])
        self.assertNotIn("8:00 AM", cancelled["found"]["text"])
        self.assertNotIn("Section 508", cancelled["found"]["text"])
        # The reading itself is never told a class meeting as an entry of the calendar: the sections' class days say those.
        self.assertEqual([entry["title"] for entry in self.engine.asked[-1]["payload"]["facts"]["calendar"] if entry["date"] == NEXT_TUE],
                         ["MATH 140 - all instructors"])
        # With no meeting on the calendar that day but a class of the course, it may be the class that is off: it stays in
        # front of the instructor, and the page says the classes were not looked at.
        self.google.rows = [row for row in self.google.rows if row["date"] != NEXT_TUE]
        cancelled = self.items(recording)["Next Tuesday's meeting is cancelled."]
        self.assertEqual((cancelled["found"]["state"], cancelled["group"], cancelled["found"]["text"]),
                         ("info", "you", "No meeting of this course is on your calendar on Tue, Nov 17. "
                                         "Your class meetings and office hours that day are not looked at here."))
        # Nor are office hours "the meeting" when a time was said: the grading meeting at three is still not on the calendar.
        self.engine.answer = lambda exam: {"begins_at_line": 1, "items": [
            item("date", "The grading meeting is next Tuesday at 3 pm.", "The grading meeting is Thursday at three in Blocker 220.", 5,
                 date=NEXT_TUE, time="15:00", about={"what": "calendar", "words": "Grading meeting"})]}
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        (meeting,) = self.one(recording)["items"]
        self.assertEqual((meeting["found"]["state"], meeting["found"]["text"]), ("open", "Not on your calendar on Tue, Nov 17."))

    def test_a_day_holds_the_thing_only_by_a_near_time_or_a_like_name(self):
        # Second live reading, 2026-11-13: a dated item was "already done" because three homework deadlines fell on its day.
        self.event("MATH 140 Sect. 5.6 due", NEXT_TUE, "", "", all_day=True, category="deadline", scope="term",
                   definition_id=self.definition, term_id=self.term)
        said = lambda **about: {"begins_at_line": 1, "items": [  # noqa: E731
            item("date", "The review session is next Tuesday.", "We will not meet next Tuesday, that meeting is cancelled.", 4,
                 date=NEXT_TUE, about=about)]}
        self.engine.answer = lambda exam: said(what="calendar", words="MATH 140 review session")
        recording = self.read()
        (review,) = self.one(recording)["items"]
        self.assertEqual((review["found"]["state"], review["found"]["text"], review["found"].get("offer"), review["group"]),
                         ("open", "Nothing named like this is on your calendar on Tue, Nov 17.", "calendar", "you"))
        # An entry named like it is the thing, whatever else the day holds; the course's own code names nothing.
        self.google.add("MATH 140 - Review sessions", NEXT_TUE, "17:00", "18:00")
        (review,) = self.one(recording)["items"]
        self.assertEqual((review["found"]["state"], review["found"]["text"], review["group"]),
                         ("done", "On your calendar that day: “MATH 140 - Review sessions” 5:00 PM.", "done"))
        day = {"date": NEXT_TUE, "end_date": NEXT_TUE, "source": "google", "all_day": False, "time": "17:00"}
        self.assertTrue(checks._named({**day, "title": "MATH 140 — All-instructors meeting"}, ["all", "instructor", "meeting", "online"]))
        self.assertFalse(checks._named({**day, "title": "MATH 140 Sect. 5.3 due"}, ["exam", "3"]))   # "3" is no part of "5.3"
        self.assertFalse(checks._named({**day, "title": "MATH 140 — All-instructors meeting"}, []))
        # With neither a time nor a name, the day's entries are shown and nothing is claimed or offered.
        self.engine.answer = lambda exam: said(what="none")
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        (review,) = self.one(recording)["items"]
        self.assertEqual((review["found"], review["group"]),
                         ({"state": "info", "href": "#calendar", "text": "On your calendar that day: “MATH 140 Sect. 5.6 due”, "
                           "“MATH 140 - Review sessions” 5:00 PM. Faculty cannot tell whether one of them is this."}, "you"))

    def test_meetings_called_off_from_now_on_list_the_rest_of_the_series(self):
        for day in ("2026-11-17", "2026-11-24", "2026-12-01"):
            self.google.add("MATH 140 - all instructors", day, "10:00", "11:15")
        self.engine.answer = lambda exam: {"begins_at_line": 1, "items": [
            item("date", "There are no more instructor meetings this term.", "We will not meet next Tuesday, that meeting is cancelled.", 4,
                 happening=False, about={"what": "calendar", "words": "All instructors meeting"})]}
        recording = self.read()
        (found,) = [entry["found"] for entry in self.one(recording)["items"]]
        self.assertEqual((found["state"], found["entries"]), ("listed", 3))
        self.assertIn("Tue, Nov 17 “MATH 140 - all instructors” 10:00 AM, Tue, Nov 24", found["text"])


class BecomesTests(ReadingBase):
    """WHAT AN ITEM MAY BECOME: an Approve card, a memory by the instructor's own press, a ruling held for a rubric."""

    def test_a_date_not_on_the_calendar_becomes_one_approve_card_and_the_entry_is_made_only_by_approve(self):
        recording = self.read()
        meeting = self.items(recording)["The Exam 3 grading meeting is Thursday at 3 pm in Blocker 220."]
        self.assertEqual((meeting["found"]["text"], meeting["found"]["offer"], meeting["group"], meeting["when"]),
                         ("Not on your calendar on Thu, Nov 12.", "calendar", "you", "Thu, Nov 12 · 3:00 PM"))
        (card,) = self.cards()
        self.assertEqual(meeting["card"]["id"], card["id"])
        self.assertEqual((card["endpoint"], card["tier"], card["engine"], card["page"]),
                         ("workspace_calendar.create", "approve", "recordings", f"#recordings/{recording}?t=20000"))
        self.assertEqual({key: card["body"][key] for key in ("title", "date", "time", "all_day", "scope", "term_id")},
                         {"title": "Exam 3 grading meeting", "date": THU, "time": "15:00", "all_day": False, "scope": "term", "term_id": self.term})
        self.assertIn("“The grading meeting is Thursday at three in Blocker 220.”", card["body"]["notes"])
        events = lambda: [event for event in self.client.get("/api/calendar", query_string={"start": THU, "end": THU}).json["events"]
                          if event["title"] == "Exam 3 grading meeting"]
        self.assertEqual(events(), [])   # nothing is on the calendar until Approve
        # Read again: the same item keeps its card; no second one is made.
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([waiting["id"] for waiting in self.cards()], [card["id"]])
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", headers=HEADERS, json={"seen": card["seen"]})
        self.assertEqual((approved.status_code, approved.json["action"]["status"]), (200, "succeeded"))
        self.assertEqual(len(events()), 1)
        meeting = self.items(recording)["The Exam 3 grading meeting is Thursday at 3 pm in Blocker 220."]
        self.assertEqual((meeting["found"]["state"], meeting["found"]["text"], meeting["group"]),
                         ("done", "On your calendar that day: “Exam 3 grading meeting” 3:00 PM.", "done"))

    def test_a_date_of_something_a_record_owns_is_shown_against_the_calendar_and_never_offered_as_an_entry(self):
        self.engine.answer = lambda exam: {"begins_at_line": 1, "items": [
            item("date", "Exam 3 grades are due next Tuesday.", "We will not meet next Tuesday, that meeting is cancelled.", 4,
                 date=NEXT_TUE, about={"what": "assessment", "assessment": exam})]}
        recording = self.read()
        (due,) = self.one(recording)["items"]
        self.assertEqual((due["found"], due["group"]),
                         ({"state": "open", "text": "Not on your calendar on Tue, Nov 17.", "href": "#calendar"}, "you"))
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.post(f"{recording}/items/{due['id']}/card").status_code, 409)

    def test_a_card_that_lapsed_is_proposed_again_only_on_the_instructors_press(self):
        recording = self.read()
        meeting = self.items(recording)["The Exam 3 grading meeting is Thursday at 3 pm in Blocker 220."]
        (card,) = self.cards()
        self.assertEqual(self.post(f"{recording}/items/{meeting['id']}/card").status_code, 409)   # its card is waiting
        self.assertEqual(self.client.post(f"/api/assistant/actions/{card['id']}/decline", headers=HEADERS, json={}).status_code, 200)
        self.round(later=700)
        self.assertEqual(self.cards(), [])   # declined: no round proposes it again
        again = self.post(f"{recording}/items/{meeting['id']}/card", request_id="again-1")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.post(f"{recording}/items/{meeting['id']}/card", request_id="again-1").status_code, 200)
        (second,) = self.cards()
        self.assertNotEqual(second["id"], card["id"])
        # An item that names no missing day has nothing to propose.
        policy_item = self.items(recording)["Late work is never accepted after solutions are posted."]
        self.assertEqual(self.post(f"{recording}/items/{policy_item['id']}/card").status_code, 409)

    def test_filed_elsewhere_its_cards_are_taken_back_and_it_is_read_again_where_it_is_now(self):
        recording = self.read()
        (card,) = self.cards()
        self.assertEqual(self.file("rec-meeting", kind="training", definition_id=self.definition).status_code, 200)
        self.assertEqual(self.cards(), [])
        page = self.one(recording)
        self.assertEqual((page["reading"]["state"], page["items"]), ("not_read", []))
        self.assertEqual(self.file("rec-meeting", kind="course", definition_id=self.definition).status_code, 200)
        self.round()
        self.assertEqual([(run["run"], run["filed_as"]) for run in self.runs()], [(1, f"term:{self.term}"), (2, f"course:{self.definition}")])
        self.assertEqual(len(self.cards()), 1)   # proposed again for where it is filed now
        self.assertNotEqual(self.cards()[0]["id"], card["id"])
        # Filed back under the term, it is shown the reading it had there: nothing is asked again.
        asked = len(self.engine.asked)
        self.assertEqual(self.file("rec-meeting", kind="term", term_id=self.term).status_code, 200)
        self.round(later=700)
        page = self.one(recording)
        self.assertEqual((len(self.engine.asked), page["reading"]["state"], page["reading"]["run"], len(page["items"])), (asked, "read", 1, 10))

    def test_a_read_recording_that_is_not_kept_is_erased_its_waiting_card_taken_back_and_what_was_remembered_stands(self):
        """ONLY WHAT IS FACULTY'S IS KEPT (requirement, 2026-11-14): Don't keep, of a meeting Faculty had read."""
        recording = self.read()
        (card,) = self.cards()
        rule = self.items(recording)["Late work is never accepted after solutions are posted."]
        with answering():
            self.assertEqual(self.post(f"{recording}/items/{rule['id']}/remember", content="Late work is not accepted once solutions are posted.").status_code, 200)
        before = self.rows()
        for table in ("recording_readings", "recording_items", "recording_item_readings", "recording_item_events", "recording_lines", "recording_files"):
            self.assertGreater(before[table], 0, table)
        self.assertEqual(self.discard("rec-meeting").status_code, 200)
        # Every row of it is gone: the reading, its items and what became of them, the transcript's lines, its files.
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {})
        for address in (f"/api/recordings/{recording}", f"/api/recordings/{recording}/audio"):
            self.assertEqual(self.client.get(address).status_code, 404, address)
        # The card that waited was taken back, saying why, and nothing went on the calendar.
        self.assertEqual(self.cards(), [])
        with self.db() as db:
            last = db.execute("SELECT kind, actor, detail_json FROM assistant_action_events WHERE action_id=? ORDER BY id DESC LIMIT 1", (card["id"],)).fetchone()
            (memory,) = [dict(row) for row in db.execute("SELECT * FROM assistant_memory_latest")]
        self.assertEqual((last["kind"], last["actor"], json.loads(last["detail_json"])), ("expired", "faculty", {"error": "This recording is no longer kept.", "superseded": True}))
        events = [event for event in self.client.get("/api/calendar", query_string={"start": THU, "end": THU}).json["events"] if event["title"] == "Exam 3 grading meeting"]
        self.assertEqual(events, [])
        # What the instructor chose to remember is their own memory, and stands.
        self.assertEqual((memory["content"], memory["source"], memory["status"]), ("Late work is not accepted once solutions are posted.", "recording", "current"))
        # No ruling of it is held for the rubric any more, and nothing counts as waiting for the instructor.
        self.assertEqual(self.record.attention()["count"], 0)
        # It is not read again, and nothing of it is asked of the engine again.
        asked = len(self.engine.asked)
        self.round(later=700)
        self.assertEqual((len(self.engine.asked), self.held("rec-meeting")), (asked, False))

    def test_what_was_approved_out_of_a_recording_stands_when_it_is_erased(self):
        recording = self.read()
        (card,) = self.cards()
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", headers=HEADERS, json={"seen": card["seen"]})
        self.assertEqual((approved.status_code, approved.json["action"]["status"]), (200, "succeeded"))
        self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertFalse(self.held("rec-meeting"))
        events = [event for event in self.client.get("/api/calendar", query_string={"start": THU, "end": THU}).json["events"] if event["title"] == "Exam 3 grading meeting"]
        self.assertEqual(len(events), 1)   # the entry they approved is theirs
        with self.db() as db:
            last = db.execute("SELECT kind FROM assistant_action_events WHERE action_id=? ORDER BY id DESC LIMIT 1", (card["id"],)).fetchone()
        self.assertEqual(last["kind"], "succeeded")   # a settled card is not touched

    def test_remember_keeps_a_policy_as_a_memory_only_by_the_instructors_own_press(self):
        recording = self.read()
        rule = self.items(recording)["Late work is never accepted after solutions are posted."]
        self.assertEqual((rule["kind"], rule["group"], rule["memory"]), ("policy", "you", None))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assistant_memory_items").fetchone()[0], 0)   # never by itself
        with answering():
            kept = self.post(f"{recording}/items/{rule['id']}/remember", request_id="keep-1",
                             content="Late work is not accepted once solutions are posted.")
            self.assertEqual(kept.status_code, 200, kept.json)
            self.assertEqual(self.post(f"{recording}/items/{rule['id']}/remember", request_id="keep-1").status_code, 200)
            self.assertEqual(self.post(f"{recording}/items/{rule['id']}/remember").status_code, 409)   # already kept
        with self.db() as db:
            (memory,) = [dict(row) for row in db.execute("SELECT * FROM assistant_memory_latest")]
        self.assertEqual((memory["content"], memory["source"], memory["scope"], memory["term_id"], memory["actor"], memory["status"]),
                         ("Late work is not accepted once solutions are posted.", "recording", "term", self.term, "instructor", "current"))
        rule = self.items(recording)[rule["sentence"]]
        self.assertEqual((rule["state"], rule["group"], rule["memory"]["memory_id"]), ("done", "done", memory["memory_id"]))
        # Only a standing rule is kept.
        todo = self.items(recording)["Publish the Exam 3 review module on Canvas."]
        self.assertEqual(self.post(f"{recording}/items/{todo['id']}/remember").status_code, 400)
        # Opened again, it is not kept a second time while that memory stands; once the instructor has removed the memory it
        # may be kept anew, for the course in every term when they widen it.
        self.assertEqual(self.post(f"{recording}/items/{rule['id']}", state="reopened").status_code, 200)
        with answering():
            self.assertEqual(self.post(f"{recording}/items/{rule['id']}/remember", scope="course").status_code, 409)
            self.app.extensions["assistant"].memory.remove(memory["memory_id"])
            self.assertEqual(self.post(f"{recording}/items/{rule['id']}/remember", scope="course").status_code, 200)
        with self.db() as db:
            self.assertEqual(sorted((row["scope"], row["status"]) for row in db.execute("SELECT scope, status FROM assistant_memory_latest")),
                             [("course", "current"), ("term", "removed")])

    def test_words_naming_a_student_are_never_kept_from_a_recording(self):
        recording = self.read()
        rule = self.items(recording)["Late work is never accepted after solutions are posted."]
        with answering():
            refused = self.post(f"{recording}/items/{rule['id']}/remember", content="Alice Example may hand in late work.")
        self.assertEqual(refused.status_code, 400, refused.json)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assistant_memory_items").fetchone()[0], 0)
        self.assertEqual(self.items(recording)[rule["sentence"]]["group"], "you")   # nothing became of it

    def test_the_assistant_settles_an_item_only_on_a_card_and_never_keeps_a_memory(self):
        self.assertEqual(policy.base_rule("recordings.remember", {}).tier, "never")
        for endpoint in ("recordings.item", "recordings.read", "recordings.card", "recordings.coordinator"):
            rule = policy.base_rule(endpoint, {})
            self.assertEqual((rule.tier, rule.request_id), ("approve", True), endpoint)

    def test_a_ruling_waits_for_its_assessments_rubric_and_is_never_offered_on_a_certified_plan(self):
        recording = self.read()
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.exam["id"]})["id"]
        held = self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json
        self.assertEqual((held["title"], held["certified"], len(held["rulings"])), ("Exam 3", False, 1))
        (ruling,) = held["rulings"]
        self.assertEqual({key: ruling[key] for key in ("sentence", "quote", "speaker", "at", "day", "question", "href", "state")},
                         {"sentence": "On question 5, the correct derivative earns two points even when its sign is wrong.",
                          "quote": "give two points for the correct derivative even when the sign is wrong.", "speaker": "Avery Quill",
                          "at": "0:24", "day": "Tue, Nov 10", "question": "5", "href": f"#recordings/{recording}?t=24000", "state": "waiting"})
        # Words that the rubric is not final are the warning where it is imported.
        self.assertEqual(held["not_final"]["quote"], "The Exam 3 rubric is not final yet, I am still changing it.")
        # Accepting records the instructor's choice and nothing else: no rubric, clarification or grade is touched.
        accepted = self.post(f"{recording}/items/{ruling['id']}", state="accepted", assignment=plan)
        self.assertEqual(accepted.status_code, 200, accepted.json)
        self.assertEqual(self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json["rulings"][0]["state"], "accepted")
        self.assertEqual(self.items(recording)[ruling["sentence"]]["group"], "done")
        todo = self.items(recording)["Publish the Exam 3 review module on Canvas."]
        self.assertEqual(self.post(f"{recording}/items/{todo['id']}", state="accepted", assignment=plan).status_code, 400)
        self.assertEqual(self.client.get("/api/recordings/rulings", query_string={"assignment": "none"}).status_code, 404)
        # Certified: nothing is offered, and nothing is accepted.
        with mock.patch("grading.progress.latest_certification", return_value={"id": "c1"}):
            certified = self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json
            self.assertEqual((certified["certified"], certified["rulings"], certified["not_final"]), (True, [], None))
            self.assertEqual(self.post(f"{recording}/items/{ruling['id']}", state="declined", assignment=plan).status_code, 409)

    def test_a_name_is_an_assessment_only_when_it_holds_every_word_of_its_title(self):
        term = {"final": "Final", "quiz": "Quiz", "quiz2": "Quiz 2", "exam3": "Exam 3", "exam13": "Exam 13",
                "home": "Take Home Assignment", "home2": "Take Home Assignment 2"}
        named = lambda name: reading.named(name, term, apart=reading.name_words("MATH 140"))  # noqa: E731
        self.assertEqual((named("final exam"), named("the final"), named("Final examination")), (["final"], ["final"], ["final"]))
        # The title must be at least half of the name: a name that is mostly of something else is not that assessment.
        self.assertEqual((named("final exam review"), named("final answer on question 20")), ([], []))
        # Only the longest title a name holds, and a number is only itself.
        self.assertEqual((named("quiz 2"), named("the quiz"), named("exam 3"), named("MATH 140 Exam 13")), (["quiz2"], ["quiz"], ["exam3"], ["exam13"]))
        self.assertEqual((named("take home assignment 2"), named("the take home assignment")), (["home2"], ["home"]))
        self.assertEqual((named("exam three"), named("Exam III"), named("quiz two"), named("exam thirteen")), (["exam3"], ["exam3"], ["quiz2"], ["exam13"]))
        # Nothing of that name, no name, or only the course's code: nothing is guessed.
        self.assertEqual((named("Financial Decision Paperwork"), named(""), named("MATH 140"), reading.named("final", {})), ([], [], [], []))
        self.assertEqual(reading.named("quizzes", {"a": "Quiz", "b": "Quiz"}), ["a", "b"])   # the same title twice: both
        # What the reading gives of an assessment the facts do not list is kept as its name, and nothing else is.
        facts = {"assessments": [{"id": "a1", "title": "Exam 3"}]}
        self.assertEqual(reading._about({"what": "assessment", "assessment": "", "words": " Financial  Decision Paperwork ", "question": "5"}, facts),
                         {"what": "assessment", "assessment": "", "words": "Financial Decision Paperwork", "question": "5"})
        self.assertEqual(reading._about({"what": "rubric", "assessment": "a9", "words": "final exam", "final": False}, facts),
                         {"what": "rubric", "assessment": "", "words": "final exam", "final": False})
        self.assertEqual(reading._about({"what": "assessment", "assessment": "a1", "words": "final exam", "question": ""}, facts),
                         {"what": "assessment", "assessment": "a1", "question": ""})   # a listed one is its id, never also a name
        for nothing in ({"what": "assessment", "assessment": "", "words": ""}, {"what": "seating", "assessment": "", "words": "final exam"},
                        {"what": "testing_center", "assessment": "a9", "words": "final exam"}):
            self.assertEqual(reading._about(nothing, facts), {"what": "none"})

    def test_a_ruling_of_an_assessment_the_reading_had_no_listing_for_is_held_by_the_name_it_was_called_by(self):
        # Second live readings, 2026-11-13: four rulings named no assessment (a paper kept on Canvas; the final exam, listed
        # as "Final") and would never have been offered at any rubric.
        self.engine.answer = lambda exam: {"begins_at_line": 1, "items": [
            item("ruling", "On question 5, the correct derivative earns two points even when its sign is wrong.",
                 "give two points for the correct derivative even when the sign is wrong.", 6,
                 about={"what": "assessment", "assessment": "", "words": "the third exam, Exam 3", "question": "5"}),
            item("ruling", "The paperwork is graded for correctness.", "From now on, late work is never accepted after solutions are posted.", 8,
                 about={"what": "assessment", "assessment": "not-an-id", "words": "Financial Decision Paperwork"}),
            item("ruling", "A ruling that names nothing.", "I will send the answer key by Friday.", 9, about={"what": "assessment", "assessment": ""}),
            item("todo", "Hand back the paperwork.", "Everyone publish the Exam 3 review module on Canvas.", 11,
                 about={"what": "assessment", "assessment": "", "words": "Financial Decision Paperwork"})]}
        recording = self.read()
        items = self.items(recording)
        exam, paper, nothing, todo = (items[sentence] for sentence in (
            "On question 5, the correct derivative earns two points even when its sign is wrong.", "The paperwork is graded for correctness.",
            "A ruling that names nothing.", "Hand back the paperwork."))
        # The page says where each one is held; all three stay rulings, and none is anything Faculty did.
        self.assertEqual((exam["group"], exam["assessment"], exam["about"], exam["found"]),
                         ("rulings", "Exam 3", {"what": "assessment", "assessment": "", "words": "the third exam, Exam 3", "question": "5"},
                          {"state": "info", "href": "", "text": "Held for “Exam 3”: matched by the name it was called by, “the third exam, Exam 3”."}))
        self.assertEqual((paper["group"], paper["assessment"], paper["found"]["text"]),
                         ("rulings", "", "No assessment of this term is called “Financial Decision Paperwork”. "
                                         "This is offered at its rubric's finalization once one is."))
        self.assertEqual((nothing["group"], nothing["about"], nothing["found"]), ("rulings", {"what": "none"}, None))
        self.assertEqual((todo["group"], todo["found"]), ("you", None))   # only what is said of GRADING waits for a rubric
        # At the rubric of the assessment that name is, it is offered like any other, and says the name.
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.exam["id"]})["id"]
        (held,) = self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json["rulings"]
        self.assertEqual((held["id"], held["named"], held["question"], held["state"]), (exam["id"], "the third exam, Exam 3", "5", "waiting"))
        # A plan the name is not is refused the ruling; the one it is may accept it.
        self.assertEqual(self.post(f"{recording}/items/{paper['id']}", state="accepted", assignment=plan).status_code, 400)
        self.assertEqual(self.post(f"{recording}/items/{exam['id']}", state="accepted", assignment=plan).status_code, 200)
        self.assertEqual(self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json["rulings"][0]["state"], "accepted")
        # An assessment of that name made AFTER the meeting: the ruling is offered at its rubric, with no new reading.
        asked = len(self.engine.asked)
        paperwork = self.app.extensions["documents"].create_assignment(self.definition, "paperwork", "Financial Decision Paperwork")["id"]
        event = self.exams.create({"term_id": self.term, "assignment_id": paperwork, "section_ids": list(self.sections.values())})
        late = self.app.extensions["frq_grading"].create_assignment({"event_id": event["id"]})["id"]
        (held,) = self.client.get("/api/recordings/rulings", query_string={"assignment": late}).json["rulings"]
        self.assertEqual((held["id"], held["named"], held["sentence"]), (paper["id"], "Financial Decision Paperwork", "The paperwork is graded for correctness."))
        paper = self.items(recording)["The paperwork is graded for correctness."]
        self.assertEqual((paper["assessment"], paper["found"]["text"]),
                         ("Financial Decision Paperwork", "Held for “Financial Decision Paperwork”: matched by the name it was called by, “Financial Decision Paperwork”."))
        self.assertEqual(len(self.engine.asked), asked)
        self.assertEqual([entry["id"] for entry in self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json["rulings"]], [exam["id"]])

    def test_an_assignment_of_the_course_with_no_date_yet_is_among_the_terms_assessments(self):
        # Second live readings, 2026-11-13: the final exam was the course's "Final" with no date in the term yet, so the reading
        # was never told of it and "final exam" named nothing.
        final = self.app.extensions["documents"].create_assignment(self.definition, "final", "Final")["id"]
        points, workout = "Each multiple-choice question on the final exam is worth three points.", "Workout questions on the final are worth five points."
        self.engine.answer = lambda exam: {"begins_at_line": 1, "items": [
            item("ruling", points, "give two points for the correct derivative even when the sign is wrong.", 6,
                 about={"what": "assessment", "assessment": "", "words": "final exam"}),
            item("ruling", workout, "I will send the answer key by Friday.", 9, about={"what": "assessment", "assessment": "a2"})]}
        recording = self.read()
        self.assertEqual(self.engine.asked[0]["payload"]["facts"]["assessments"],
                         [{"id": "a1", "title": "Exam 3", "given": "2026-11-19"}, {"id": "a2", "title": "Final", "given": ""}])
        items = self.items(recording)
        self.assertEqual((items[points]["assessment"], items[points]["found"]["text"]),
                         ("Final", "Held for “Final”: matched by the name it was called by, “final exam”."))
        self.assertEqual((items[workout]["assessment"], items[workout]["about"]["assessment"], items[workout]["found"]), ("Final", final, None))
        # Once the term gives it a date and a graded plan, both wait at its rubric: the one by its id, the other by its name.
        event = self.exams.create({"term_id": self.term, "assignment_id": final, "section_ids": list(self.sections.values())})
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": event["id"]})["id"]
        held = self.client.get("/api/recordings/rulings", query_string={"assignment": plan}).json
        self.assertEqual((held["title"], [(entry["sentence"], entry["named"]) for entry in held["rulings"]]),
                         ("Final", [(points, "final exam"), (workout, "")]))

    def test_a_terms_coordinator_is_a_name_a_later_reading_is_told(self):
        named = self.post("coordinator", term_id=self.term, name="  Avery   Quill ", request_id="who-1")
        self.assertEqual((named.status_code, named.json), (200, {"term_id": self.term, "name": "Avery Quill"}))
        self.assertEqual(self.post("coordinator", term_id=self.term, name="Someone Else", request_id="who-1").json["name"], "Someone Else")
        recording = self.read()
        self.assertEqual(self.engine.asked[0]["payload"]["facts"]["coordinator"], "Avery Quill")
        self.assertEqual(self.one(recording)["coordinator"], {"term_id": self.term, "name": "Avery Quill", "term": "MATH 140 · Fall 2026"})
        self.assertEqual(self.post("coordinator", term_id=self.term, name="").json["name"], "")   # taken back
        self.assertEqual(self.post("coordinator", term_id="nothing", name="A").status_code, 404)
        with self.db() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT revision, name FROM recording_coordinators ORDER BY revision")],
                             [(1, "Avery Quill"), (2, "")])

    def test_the_course_cards_coordinator_is_the_one_a_reading_is_told(self):
        # COORDINATOR "ONE NAME" (requirement, 2026-11-15).
        self.post("coordinator", term_id=self.term, name="Avery Quill", request_id="who-1")

        def card(answer):
            course = self.client.get("/api/state").json["courses"][0]
            saved = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "coordinator": answer}, headers=HEADERS)
            self.assertEqual(saved.status_code, 200, saved.json)

        card({"coordinated": True, "name": "Dr. Jordan Lee", "email": "jlee@tamu.edu"})
        recording = self.read()
        self.assertEqual(self.engine.asked[0]["payload"]["facts"]["coordinator"], "Dr. Jordan Lee")
        self.assertEqual(self.one(recording)["coordinator"],
                         {"term_id": self.term, "name": "Dr. Jordan Lee", "term": "MATH 140 · Fall 2026", "card": True})
        # The card's name is changed on the card: Recordings takes no other name for that term.
        refused = self.post("coordinator", term_id=self.term, name="Someone Else", request_id="who-2")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("course card", refused.json["error"])
        # Unticked, the name alone kept here stands again, as it was recorded.
        card({"coordinated": False, "name": "", "email": ""})
        self.assertEqual(self.one(recording)["coordinator"], {"term_id": self.term, "name": "Avery Quill", "term": "MATH 140 · Fall 2026"})
        with self.db() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT revision, name FROM recording_coordinators ORDER BY revision")],
                             [(1, "Avery Quill")])

    def test_the_list_counts_what_each_recording_needs_of_the_instructor(self):
        recording = self.read()
        listed = self.listing()
        row = next(entry for entry in listed["recordings"] if entry["id"] == recording)
        page = self.one(recording)
        # Open for the instructor: the seating charts, the meeting to add, the cancelled one (nothing on Google, so done), the
        # rule to remember, the rubric note, the Canvas module, the gradebook.
        self.assertEqual(page["counts"], {"you": 6, "answers": 1, "rulings": 1, "others": 1, "done": 1})
        self.assertEqual((row["for_you"], listed["attention"]["for_you"], listed["attention"]["count"]), (6, 6, 6))
        self.assertEqual(self.record.attention()["for_you"], 6)
        for entry in page["items"]:
            if entry["group"] == "you":
                self.assertEqual(self.post(f"{recording}/items/{entry['id']}", state="done").status_code, 200)
        self.assertEqual((self.listing()["attention"]["for_you"], self.one(recording)["counts"]["you"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
