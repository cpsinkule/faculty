"""Recordings, step 5: a recorded class (docs/decisions/recordings.md, RECORDINGS "Classes (step 5)"; docs/decisions/scheduling.md,
SCHEDULING "CLASS SCHEDULE: WHAT A RECORDING REACHED").

Against tests/fake_intake.py and a SCRIPTED engine in the Assistant's place: no check asks a model, reaches Plaud or reads a real
recording. The class, its words and everyone in it are invented.
"""
import json
import unittest
import uuid
from unittest import mock

from test_recordings import ALICE, BOB, HEADERS, MON, WED, campus
from test_recordings_reading import MEETING, Engine, ReadingBase, item, said

from assistant import policy
from recordings import classes, reading, readings

FRI, NEXT_WED, EXAM_DAY, BEFORE_THANKSGIVING = "2026-11-13", "2026-11-18", "2026-11-19", "2026-11-25"
OUTLINE = {number: {"title": title, "chapter": "5", "chapter_title": "Functions", "url": f"/notes/{number}"} for number, title in (
    ("5.6", "Exponential Functions"), ("5.7", "Combining Functions"), ("5.8", "Logarithms"))}

# A class, as Plaud would give it (the shape of its own transcripts; nobody here is real).
LESSON = [
    said(0, "Chris", "Speaker 1", "Alice, your makeup is Friday, find me after class."),
    said(4, "Chris", "Speaker 1", "Good morning everybody. Today we finish five six, exponential equations."),
    said(8, "Chris", "Speaker 1", "Example eight, both sides have the same base."),
    said(12, "Speaker 2", "Speaker 2", "Is this on the exam?"),
    said(16, "Chris", "Speaker 1", "Yes. Now five seven, combining functions."),
    said(20, "Chris", "Speaker 1", "We will get to five eight on Friday, we are out of time."),
    said(24, "Chris", "Speaker 1", "Remember exam three is next Wednesday night."),
    said(28, "Chris", "Speaker 1", "The homework for five six is due Friday."),
    said(32, "Chris", "Speaker 1", "There is no class the day before Thanksgiving."),
    said(36, "Chris", "Speaker 1", "Bob Example, your quiz retake is Friday too."),
    said(40, "Chris", "Speaker 1", "I will post the review sheet at some point."),
]
SIX, SEVEN = "The class worked through section 5.6, Exponential Functions.", "The class worked through section 5.7, Combining Functions."
EXAM, HOMEWORK, NO_CLASS = "You told the class Exam 3 is next Wednesday night.", "You told the class the 5.6 homework is due Friday.", \
    "You told the class there is no class the day before Thanksgiving."


def told(sentence, quote, line, day, what="none", work="", **more):
    return {"kind": "announced", "sentence": sentence, "quote": quote, "line": line, "date": day, "time": "", "date_words": "",
            "happening": True, "about": {"what": what, "notes": [], "work": work}, **more}


def covered(sentence, quote, line, *numbers):
    return {"kind": "covered", "sentence": sentence, "quote": quote, "line": line, "date": "", "time": "", "date_words": "",
            "happening": True, "about": {"what": "notes", "notes": list(numbers), "work": ""}}


def lesson(request, days=True):
    """What the scripted engine answers for LESSON: two sections worked through and three days told to the class, and beside
    them what a class reading must never keep."""
    work = {entry["title"]: entry["id"] for entry in request["payload"]["facts"]["work"]}
    found = [
        covered(SIX, "Today we finish five six, exponential equations.", 1, "5.6"),
        covered(SEVEN, "Now five seven, combining functions.", 4, "5.7"),
        # Named only as coming on another day, and answered with a section the notes do not have: no item.
        covered("The class worked through section 5.9.", "We will get to five eight on Friday, we are out of time.", 5, "5.9", "five eight"),
    ]
    if days:
        found += [
            told(EXAM, "Remember exam three is next Wednesday night.", 6, NEXT_WED, "work", work.get("Exam 3", "")),
            told(HOMEWORK, "The homework for five six is due Friday.", 7, FRI, "work", work.get("Homework 5.6", "")),
            told(NO_CLASS, "There is no class the day before Thanksgiving.", 8, BEFORE_THANKSGIVING, "class", happening=False),
            # NOTHING ABOUT ONE STUDENT, whoever is named or heard: before class began, by name, and as a task.
            told("A makeup is on Friday.", "Alice, your makeup is Friday, find me after class.", 0, FRI),
            told("A quiz retake is on Friday.", "Bob Example, your quiz retake is Friday too.", 9, FRI),   # said to him: his name is in its words
            item("todo", "Post the review sheet.", "I will post the review sheet at some point.", 10),
            told("The review sheet is posted at some point.", "I will post the review sheet at some point.", 10, ""),
        ]
    return {"begins_at_line": 1, "items": found}


class ClassEngine(Engine):
    """The scripted engine, answering from the request itself (a class's facts list its work, not the term's assessments)."""

    def submit(self, request):
        self.asked.append(request)
        if self.error:
            raise self.error
        return {"state": "completed", "result": {"output": self.answer(request), "metadata": {"requested_model": "scripted-model"}}}


class ClassBase(ReadingBase):
    def setUp(self):
        super().setUp()
        self.engine = ClassEngine()
        self.engine.answer = lesson
        notes = mock.patch("course_notes.service.current_sections", return_value=OUTLINE)
        notes.start()
        self.addCleanup(notes.stop)
        self.alice, self.bob = self.students[ALICE], self.students[BOB]
        # Both sections meet Monday, Wednesday and Friday, as the Calendar shows it.
        self.schedule({"kind": "class", "weekdays": [0, 2, 4], "start": "08:00", "end": "08:50", "room": "HELD 107", "section_id": self.sections[507]},
                      {"kind": "class", "weekdays": [0, 2, 4], "start": "09:10", "end": "10:00", "room": "HELD 107", "section_id": self.sections[508]})

    def plan(self, **change):
        url = f"/api/scheduling/terms/{self.term}/class-schedule"
        saved = self.client.post(url, headers=HEADERS, json={"request_id": str(uuid.uuid4()), "base_revision": self.client.get(url).json["revision"], **change})
        self.assertEqual(saved.status_code, 200, saved.json)
        return saved.json

    def topics(self, day, *numbers, **sections):
        """The topics planned for a class day: the term's, and a section's own where given."""
        return self.plan(topics={day: {"notes": list(numbers), "note": "",
                                       "sections": {self.sections[number]: {"notes": own, "note": ""} for number, own in ((int(key[1:]), value) for key, value in sections.items())}}})

    def taught(self, plaud_id="rec-class", day=WED, clock="08:01", **more):
        """A recording that began inside a section's class meeting: filed on that class day, kept and read in the same round."""
        self.fake.add(plaud_id, campus(day, clock), minutes=49, title="Recording", **{"transcript": LESSON, **more})
        self.assertEqual(self.round()["status"], "ok")
        return self.item(plaud_id)["id"]

    def day(self, recording):
        return self.one(recording)["class_day"]

    def moves(self):
        return [card for card in self.cards() if card["engine"] == "recordings" and card["endpoint"] == "scheduling.class_plan_save"]

    def approve(self, card):
        return self.client.post(f"/api/assistant/actions/{card['id']}/approve", headers=HEADERS, json={"seen": card["seen"]})

    def decline(self, card):
        return self.client.post(f"/api/assistant/actions/{card['id']}/decline", headers=HEADERS, json={"seen": card["seen"]})

    def shown(self, section, day):
        """One class day as the section's class schedule shows it."""
        answer = self.client.get(f"/api/scheduling/sections/{self.sections[section]}/class-schedule", query_string={"start": MON, "end": FRI})
        self.assertEqual(answer.status_code, 200, answer.json)
        return next(entry for entry in answer.json["days"] if entry["date"] == day)

    def own(self, day, section=507):
        return self.client.get(f"/api/scheduling/terms/{self.term}/class-schedule").json["topics"].get(day, {}).get("sections", {}).get(self.sections[section])


class ReadingTests(ClassBase):
    def test_a_class_is_read_by_its_own_instructions_and_makes_no_student_link(self):
        self.plan(set=[{"kind": "homework", "title": "Homework 5.6", "sections": [], "date": FRI}])
        recording = self.taught()
        self.assertEqual(self.item("rec-class")["name"], "Class · MATH 140 Section 507")
        (request,) = self.engine.asked
        self.assertEqual((request["prompt"], request["schema"], request["durable"]), (reading.CLASS_PROMPT, reading.CLASS_SCHEMA, False))
        facts = request["payload"]["facts"]
        # What a class reading is told, each read through its owner: the course's notes by number and the TITLES of the
        # section's work. No date Faculty holds, no planned topics, no calendar, no coordinator and nothing of any student.
        self.assertEqual(set(facts), {"recorded", "instructor", "speakers", "course", "term", "section", "notes", "work"})
        self.assertEqual((facts["recorded"], facts["section"], facts["instructor"]),
                         ({"date": WED, "weekday": "Wednesday", "began": "08:01"}, 507, {"name": "Chris Example", "speaker": "Chris"}))
        self.assertEqual(facts["notes"], [{"number": "5.6", "title": "Exponential Functions"}, {"number": "5.7", "title": "Combining Functions"},
                                          {"number": "5.8", "title": "Logarithms"}])
        self.assertEqual(sorted(entry["title"] for entry in facts["work"]), ["Exam 3", "Homework 5.6"])
        told_facts = json.dumps(facts)
        self.assertTrue(all(word not in told_facts for word in (EXAM_DAY, FRI, "Alice", "Bob", "alice@")), told_facts)
        (run,) = self.runs()
        self.assertEqual((run["status"], run["version"], run["filed_as"]), ("read", reading.CLASS_VERSION, f"class_day:{self.sections[507]}:{WED}"))
        page = self.one(recording)
        self.assertEqual((page["meeting"], page["reading"]["state"], page["students"]), ("class", "read", []))
        # Two kinds of item and no other; who spoke and when come from the transcript.
        self.assertEqual({entry["sentence"]: (entry["kind"], entry["group"], entry["speaker"], entry["start_ms"]) for entry in page["items"]}, {
            SIX: ("covered", "covered", "Chris", 4000), SEVEN: ("covered", "covered", "Chris", 16000),
            EXAM: ("announced", "you", "Chris", 24000), HOMEWORK: ("announced", "told", "Chris", 28000), NO_CLASS: ("announced", "you", "Chris", 32000)})
        self.assertEqual([entry["about"] for entry in page["items"] if entry["kind"] == "covered"],
                         [{"what": "notes", "notes": ["5.6"]}, {"what": "notes", "notes": ["5.7"]}])
        self.assertEqual({dropped["sentence"]: dropped["reason"] for dropped in page["reading"]["dropped"]}, {
            "The class worked through section 5.9.": "it names no section of the course's notes",
            "A makeup is on Friday.": "said before the meeting began",
            "": "it names a student: a class recording raises nothing about one student",   # and not even its words are kept
            "Post the review sheet.": "not a kind of item this meeting is read for",
            "The review sheet is posted at some point.": "it names no day"})
        self.assertNotIn("Bob", json.dumps([page["items"], page["reading"]["dropped"]]))
        # A CLASS RECORDING MAKES NO STUDENT LINK: it is filed on the class day alone and is on no student's timeline.
        with self.db() as db:
            kinds = [row[0] for row in db.execute("SELECT DISTINCT kind FROM recording_links")]
        self.assertEqual(kinds, ["class_day"])
        for enrollment in (self.alice, self.bob):
            entries = self.client.get(f"/api/communications/students/{enrollment}").json["entries"]
            self.assertEqual([entry for entry in entries if entry["channel"] == "meeting"], [])
        # It raises nothing else: no memory can be kept from it, and no calendar entry is proposed for a day told to the class.
        six = next(entry for entry in page["items"] if entry["sentence"] == SIX)
        self.assertEqual(self.post(f"{recording}/items/{six['id']}/remember").status_code, 400)
        self.assertEqual([card["endpoint"] for card in self.cards() if card["engine"] == "recordings"], [])
        # A later round asks nothing again.
        self.round(later=700)
        self.assertEqual(len(self.engine.asked), 1)

    def test_filed_by_hand_on_a_class_day_it_is_read_the_same_and_a_training_is_not(self):
        # A memo is read since step 6 (tests/test_recordings_memos.py); a course's TRAINING is the filing that is kept and not read.
        self.fake.add("rec-late", campus(WED, "13:00"), transcript=LESSON)
        self.round()
        self.assertEqual(self.item("rec-late")["filing"], "needs_filing")
        self.assertEqual(self.file("rec-late", kind="training", definition_id=self.definition).status_code, 200)
        self.round(later=30)
        self.assertEqual(self.engine.asked, [])
        page = self.one(self.item("rec-late")["id"])
        self.assertEqual((page["reading"]["state"], page["reading"]["detail"], "class_day" in page), ("not_read", readings.TRAINING, False))
        self.assertEqual(self.file("rec-late", kind="class_day", section_id=self.sections[508], date=WED).status_code, 200)
        self.round(later=30)
        (request,) = self.engine.asked
        self.assertEqual((request["prompt"], request["payload"]["facts"]["section"]), (reading.CLASS_PROMPT, 508))
        self.assertEqual(self.day(self.item("rec-late")["id"])["section"], 508)

    def test_the_quote_check_keeps_only_a_class_readings_two_kinds(self):
        lines = [{"line": 0, "start_ms": 0, "end_ms": 4000, "speaker_key": "s1", "speaker": "Chris", "text": "Today we start five eight and the quiz is Friday."}]
        facts = {"notes": [{"number": "5.8", "title": "Logarithms"}], "work": [{"id": "w1", "title": "Quiz 9"}]}
        quote, day = "Today we start five eight", __import__("datetime").date(2026, 11, 11)

        def kept(*given, facts=facts, names=()):
            _, items, dropped = reading.checked({"begins_at_line": 0, "items": list(given)}, lines, facts, day, reading.CLASS_KINDS, names)
            return items, [entry["reason"] for entry in dropped]

        items, dropped = kept(covered("Section 5.8.", quote, 0, "5.8", "5.8", "6.1", "chapter five"),
                              told("Quiz 9 is Friday.", "the quiz is Friday.", 0, FRI, "work", "w1", time="09:10", date_words="Friday"),
                              told("Something is Friday.", "and the quiz is Friday.", 0, FRI, "work", "w7"))
        self.assertEqual(dropped, [])
        self.assertEqual([(entry["kind"], entry["about"], entry["date"], entry["time"], entry["for"], entry["happening"]) for entry in items], [
            ("covered", {"what": "notes", "notes": ["5.8"]}, "", "", "you", True),            # only a number the notes have, once
            ("announced", {"what": "work", "work": "w1"}, FRI, "09:10", "you", True),
            ("announced", {"what": "none"}, FRI, "", "you", True)])                           # work the facts did not list is named by nothing
        # A course with no notes outline in Faculty keeps a number as it is written, as a class day's topics do.
        self.assertEqual(kept(covered("Section 6.1.", quote, 0, "6.1"), facts={"notes": [], "work": []})[0][0]["about"], {"what": "notes", "notes": ["6.1"]})
        self.assertEqual(kept(covered("Nothing.", quote, 0), covered("Words.", "we start five eight", 0, "five eight"), told("No day.", "the quiz is Friday.", 0, ""))[1],
                         ["it names no section of the course's notes", "it names no section of the course's notes", "it names no day"])
        # A student's whole name in the sentence: dropped. A word that only looks like part of one is not judged.
        names = ["Example, Alice", "Power, Grant"]
        self.assertEqual(kept(told("Alice Example retakes the quiz Friday.", "the quiz is Friday.", 0, FRI), names=names)[1],
                         ["it names a student: a class recording raises nothing about one student"])
        named = [{**lines[0], "text": "Alice Example, your quiz is Friday. Today we start five eight."}]
        _, items, dropped = reading.checked({"begins_at_line": 0, "items": [
            told("A quiz is on Friday.", "Alice Example, your quiz is Friday.", 0, FRI),               # a day told to one student, by its words
            covered("Section 5.8.", "Alice Example, your quiz is Friday. Today we start five eight.", 0, "5.8")]}, named, facts, day, reading.CLASS_KINDS, names)
        self.assertEqual(([entry["kind"] for entry in items], [(entry["sentence"], entry["quote"]) for entry in dropped]), (["covered"], [("", "")]))
        self.assertEqual(len(kept(covered("Section 5.8, Power functions and logarithms.", quote, 0, "5.8"), names=names)[0]), 1)
        # A meeting filed under a term never keeps a class's kinds, and a class never a meeting's.
        _, items, dropped = reading.checked({"begins_at_line": 0, "items": [covered("Section 5.8.", quote, 0, "5.8")]}, lines, {"assessments": []}, day)
        self.assertEqual((items, dropped[0]["reason"]), ([], "not a kind of item this meeting is read for"))

    def test_a_meeting_filed_under_the_term_is_read_as_before(self):
        self.engine = Engine()
        recording = self.read(transcript=MEETING)
        (request,) = self.engine.asked
        self.assertEqual((request["prompt"], request["schema"]), (reading.PROMPT, reading.SCHEMA))
        page = self.one(recording)
        self.assertEqual((page["meeting"], "class_day" in page, sorted(page["counts"])), ("staff", False, ["answers", "done", "others", "rulings", "you"]))


class CoveredTests(ClassBase):
    def setUp(self):
        super().setUp()
        self.engine.answer = lambda request: lesson(request, days=False)

    def test_a_day_planned_5_6_to_5_8_and_recorded_through_5_7_shows_orange_and_offers_the_move(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        recording = self.taught()
        day = self.day(recording)
        self.assertEqual((day["section"], day["date"], day["day"], day["state"], day["text"], day["next_day"], day["next"], day["own"]),
                         (507, WED, "Wed, Nov 11", "differs", "Not reached: 5.8.", FRI, "Fri, Nov 13", False))
        self.assertEqual(([entry["number"] for entry in day["planned"]], day["reached"], day["not_reached"], day["ahead"], day["earlier"]),
                         (["5.6", "5.7", "5.8"], [{"number": "5.6", "title": "Exponential Functions", "start_ms": 4000},
                                                    {"number": "5.7", "title": "Combining Functions", "start_ms": 16000}],
                          [{"number": "5.8", "title": "Logarithms"}], [], []))
        self.assertEqual(day["href"], f"#course/{self.group}/section/507/schedule")
        # ORANGE: the day needs the instructor once, on the page and in the list.
        self.assertEqual((self.one(recording)["counts"]["you"], self.item("rec-class")["for_you"], self.listing()["attention"]["for_you"]), (1, 1, 1))
        # THE MOVE IS OFFERED, on one Approve card through the class schedule's own route, and nothing is changed by itself.
        (card,) = self.moves()
        self.assertEqual((card["method"], card["path"], card["tier"], card["page"], day["card"]["id"], day["offer"]),
                         ("POST", f"/api/scheduling/terms/{self.term}/class-schedule", "approve", f"#recordings/{recording}", card["id"], False))
        mine = self.sections[507]
        self.assertEqual((card["body"]["base_revision"], card["body"]["set"], card["body"]["remove"], card["body"]["topics"]), (1, [], [], {
            WED: {"notes": ["5.6", "5.7", "5.8"], "note": "", "sections": {mine: {"notes": ["5.6", "5.7"], "note": ""}}},
            FRI: {"notes": [], "note": "", "sections": {mine: {"notes": ["5.8"], "note": ""}}}}))
        self.assertEqual(card["summary"], "Move 5.8 Logarithms from Wed, Nov 11 to Fri, Nov 13 on Section 507's class schedule: "
                                          "the recording of that class reached 5.6, 5.7.")
        self.assertEqual((self.own(WED), self.own(FRI)), (None, None))
        # THE CLASS DAY SHOWS IT on the section's own class schedule, and no other section's.
        self.assertEqual(self.shown(507, WED)["recorded"], {
            "recording_id": recording, "href": f"#recordings/{recording}", "read": True, "differs": True, "not_reached": ["5.8"],
            "reached": [{"number": "5.6", "title": "Exponential Functions"}, {"number": "5.7", "title": "Combining Functions"}]})
        self.assertNotIn("recorded", self.shown(508, WED))
        self.assertNotIn("recorded", self.shown(507, MON))
        # Read again: the same card waits; no second one is made.
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([found["id"] for found in self.moves()], [card["id"]])
        # APPROVE moves it, for this section alone.
        approved = self.approve(card)
        self.assertEqual((approved.status_code, approved.json["action"]["status"]), (200, "succeeded"), approved.json)
        self.assertEqual((self.own(WED), self.own(FRI), self.own(WED, 508)),
                         ({"notes": ["5.6", "5.7"], "note": ""}, {"notes": ["5.8"], "note": ""}, None))
        day = self.day(recording)
        self.assertEqual((day["state"], day["text"], day["not_reached"], day["own"], day["offer"], self.item("rec-class")["for_you"]),
                         ("as_planned", "Reached what was planned for this day.", [], True, False, 0))
        self.assertEqual((self.shown(507, WED)["recorded"]["differs"], [note["number"] for note in self.shown(507, FRI)["topics"]["notes"]]), (False, ["5.8"]))
        self.round(later=700)
        self.assertEqual(self.moves(), [])

    def test_a_declined_move_leaves_the_plan_and_comes_back_only_by_the_instructors_press(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        recording = self.taught()
        (card,) = self.moves()
        self.assertEqual(self.decline(card).status_code, 200)
        day = self.day(recording)
        # Their word that the plan stands: no longer orange, nothing moved, and not proposed again by Faculty.
        self.assertEqual((day["state"], day["offer"], day["card"]["status"], self.item("rec-class")["for_you"], self.shown(507, WED)["recorded"]["differs"]),
                         ("left", True, "declined", 0, False))
        self.assertEqual(self.shown(507, WED)["recorded"]["not_reached"], ["5.8"])
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.round(later=700)
        self.assertEqual((self.moves(), self.own(FRI)), ([], None))
        # Their own press proposes it again: once for a request ID, and refused while its card waits.
        pressed = self.post(f"{recording}/cards", kind="move", request_id="again-1")
        self.assertEqual(pressed.status_code, 200, pressed.json)
        self.assertEqual(self.post(f"{recording}/cards", kind="move", request_id="again-1").status_code, 200)
        (again,) = self.moves()
        self.assertNotEqual(again["id"], card["id"])
        self.assertEqual((self.day(recording)["state"], self.item("rec-class")["for_you"]), ("differs", 1))
        self.assertEqual(self.post(f"{recording}/cards", kind="move").status_code, 409)
        self.assertEqual(self.post(f"{recording}/cards", kind="other").status_code, 400)

    def test_another_sections_approved_move_does_not_strand_the_card_that_still_waits(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        first, second = self.taught("rec-507"), self.taught("rec-508", clock="09:11")
        cards = {card["page"]: card for card in self.moves()}
        self.assertEqual(set(cards), {f"#recordings/{first}", f"#recordings/{second}"})
        self.assertEqual(self.approve(cards[f"#recordings/{first}"]).json["action"]["status"], "succeeded")
        # The other card was made against the plan as it was: its Approve is refused by the schedule's own preview.
        stale = self.approve(cards[f"#recordings/{second}"])
        self.assertEqual((stale.status_code, stale.json.get("stale"), self.own(WED, 508)), (200, True, None))
        # The next round takes it back and proposes the same move against the plan as it is now.
        self.round(later=700)
        (fresh,) = self.moves()
        self.assertEqual((fresh["page"], fresh["body"]["base_revision"], fresh["id"] != cards[f"#recordings/{second}"]["id"]),
                         (f"#recordings/{second}", 2, True))
        self.round(later=700)   # a card that says what Faculty would propose now is left alone
        self.assertEqual([card["id"] for card in self.moves()], [fresh["id"]])
        self.assertEqual(self.approve(fresh).json["action"]["status"], "succeeded")
        self.assertEqual((self.own(WED, 507), self.own(WED, 508), self.own(FRI, 507), self.own(FRI, 508)),
                         ({"notes": ["5.6", "5.7"], "note": ""}, {"notes": ["5.6", "5.7"], "note": ""}, {"notes": ["5.8"], "note": ""}, {"notes": ["5.8"], "note": ""}))
        self.assertEqual(self.listing()["attention"]["for_you"], 0)

    def test_what_was_reached_earlier_or_beyond_the_plan_is_shown_and_is_no_difference(self):
        self.topics(MON, "5.6")
        self.topics(WED, "5.7", "5.8")
        # Monday's class reached 5.6 and, ahead of the plan, 5.8; Wednesday's reached 5.7 only.
        self.engine.answer = lambda request: {"begins_at_line": 1, "items": [
            covered(SIX, "Today we finish five six, exponential equations.", 1, "5.6"),
            covered("The class worked through section 5.8, Logarithms.", "We will get to five eight on Friday, we are out of time.", 5, "5.8")]}
        monday = self.taught("rec-mon", day=MON)
        self.engine.answer = lambda request: {"begins_at_line": 1, "items": [covered(SEVEN, "Now five seven, combining functions.", 4, "5.7")]}
        wednesday = self.taught("rec-wed")
        ahead, later = self.day(monday), self.day(wednesday)
        self.assertEqual((ahead["state"], [entry["number"] for entry in ahead["ahead"]], ahead["not_reached"]), ("as_planned", ["5.8"], []))
        self.assertEqual((later["state"], later["not_reached"], later["earlier"]),
                         ("as_planned", [], [{"number": "5.8", "title": "Logarithms", "date": MON, "day": "Mon, Nov 9"}]))
        self.assertEqual((self.moves(), self.listing()["attention"]["for_you"]), ([], 0))

    def test_a_day_with_nothing_planned_or_nothing_heard_is_set_against_nothing(self):
        recording = self.taught()
        day = self.day(recording)
        self.assertEqual((day["state"], day["text"], day["planned"], day["offer"], day["card"]),
                         ("unplanned", "Reached 5.6 and 5.7. No topics were planned for this day.", [], False, None))
        self.assertEqual((self.moves(), self.item("rec-class")["for_you"], self.shown(507, WED)["recorded"]["differs"]), ([], 0, False))
        # A reading that kept no notes section says nothing against a plan: a card must rest on words.
        self.topics(WED, "5.6", "5.7", "5.8")
        self.engine.answer = lambda request: {"begins_at_line": 1, "items": []}
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        day = self.day(recording)
        self.assertEqual((day["state"], day["text"], self.moves(), self.item("rec-class")["for_you"]),
                         ("none", "The reading found no notes section this class worked through, so nothing is set against the plan.", [], 0))
        self.assertEqual(self.post(f"{recording}/cards", kind="move").status_code, 409)

    def test_a_dismissed_section_is_not_reached_and_the_waiting_card_follows(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        recording = self.taught()
        (card,) = self.moves()
        seven = next(entry for entry in self.one(recording)["items"] if entry["sentence"] == SEVEN)
        self.assertEqual(self.post(f"{recording}/items/{seven['id']}", state="dismissed").status_code, 200)
        day = self.day(recording)
        self.assertEqual(([entry["number"] for entry in day["reached"]], [entry["number"] for entry in day["not_reached"]]), (["5.6"], ["5.7", "5.8"]))
        (fresh,) = self.moves()
        self.assertNotEqual(fresh["id"], card["id"])
        self.assertEqual(fresh["body"]["topics"][FRI]["sections"][self.sections[507]], {"notes": ["5.7", "5.8"], "note": ""})
        # Everything planned goes: the day keeps a note saying where, so its own entry still stands for it.
        six = next(entry for entry in self.one(recording)["items"] if entry["sentence"] == SIX)
        self.topics(WED, "5.7", "5.8")
        self.round(later=700)
        (fresh,) = self.moves()
        self.assertEqual(fresh["body"]["topics"][WED]["sections"][self.sections[507]], {"notes": [], "note": "5.7 and 5.8 moved to Fri, Nov 13"})
        self.assertEqual(six["group"], "covered")

    def test_filed_elsewhere_its_card_is_taken_back_and_the_class_day_no_longer_shows_it(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        recording = self.taught()
        self.assertEqual(len(self.moves()), 1)
        self.assertEqual(self.file("rec-class", kind="memo").status_code, 200)
        self.assertEqual((self.moves(), "recorded" in self.shown(507, WED), "class_day" in self.one(recording)), ([], False, False))
        # The last class day has no later one to move to: orange, and nothing is offered.
        self.fake.add("rec-last", campus("2026-12-02", "08:01"), minutes=49, transcript=LESSON)
        self.clock = campus("2026-12-02", "12:00")
        self.topics("2026-12-02", "5.6", "5.7", "5.8")
        self.assertEqual(self.round()["status"], "ok")
        last = self.day(self.item("rec-last")["id"])
        self.assertEqual((last["state"], last["next_day"], last["offer"], last["blocked"], self.moves()),
                         ("differs", "", False, "This section has no later class day to move it to.", []))

    def test_two_recordings_of_one_class_are_one_class_day_with_one_card(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        # The recorder was started twice: the first recording holds no section, the second where the class got to.
        self.engine.answer = lambda request: {"begins_at_line": 1, "items": []}
        first = self.taught("rec-a")
        self.assertEqual((self.day(first)["state"], self.moves()), ("none", []))
        self.engine.answer = lambda request: lesson(request, days=False)
        second = self.taught("rec-b", clock="08:30")
        day = self.day(second)
        self.assertEqual((day["state"], day["recordings"], day["carrier"], self.day(first)["state"]), ("differs", [first, second], second, "differs"))
        # ONE card for the day, on the recording that says where the class got to, which also carries the day's count.
        (card,) = self.moves()
        self.assertEqual((card["page"], self.day(first)["card"]["id"]), (f"#recordings/{second}", card["id"]))
        self.assertEqual((self.item("rec-a")["for_you"], self.item("rec-b")["for_you"], self.shown(507, WED)["recorded"]["recording_id"]), (0, 1, second))
        # Read again with less heard, the second recording's card is taken back and the day's move is proposed as it stands.
        self.engine.answer = lambda request: {"begins_at_line": 1, "items": [covered(SIX, "Today we finish five six, exponential equations.", 1, "5.6")]}
        self.assertEqual(self.post(f"{second}/read").status_code, 200)
        self.record.run_due()
        (fresh,) = self.moves()
        self.assertEqual((fresh["id"] != card["id"], fresh["body"]["topics"][FRI]["sections"][self.sections[507]]["notes"]), (True, ["5.7", "5.8"]))

    def test_a_move_the_class_schedule_would_refuse_is_never_proposed(self):
        self.topics(WED, "5.6", "5.7", "5.8")
        self.topics(FRI, *(f"6.{number}" for number in range(1, 9)))   # Friday already holds as many topics as a day may
        recording = self.taught()
        day = self.day(recording)
        self.assertEqual((day["state"], day["offer"], day["card"]),  ("differs", False, None))
        self.assertEqual(day["blocked"], "Fri, Nov 13 would then hold more than 8 topics. Edit that day's topics on the class schedule.")
        self.round(later=700)
        self.round(later=700)
        made = self.client.get("/api/assistant/actions").json
        self.assertEqual([action for action in made["pending"] + made["recent"] if action["engine"] == "recordings"], [])   # nothing was tried
        refused = self.post(f"{recording}/cards", kind="move")
        self.assertEqual((refused.status_code, refused.json["error"]), (409, day["blocked"]))
        # It still needs the instructor; room made on Friday, the move is theirs to propose.
        self.assertEqual(self.item("rec-class")["for_you"], 1)
        self.topics(FRI, "6.1")
        self.assertEqual((self.day(recording)["offer"], self.post(f"{recording}/cards", kind="move").status_code, len(self.moves())), (True, 200, 1))

    def test_a_term_no_longer_in_faculty_fails_neither_the_page_nor_the_list(self):
        self.engine.answer = lesson
        self.topics(WED, "5.6", "5.7", "5.8")
        recording = self.taught()
        self.assertEqual(self.item("rec-class")["for_you"], 3)
        with self.db() as db:
            db.execute("UPDATE course_terms SET deleted_at='2026-11-12T00:00:00+00:00' WHERE id=?", (self.term,))
        # The class schedule can no longer be read for its work: those days are set against nothing, and nothing fails.
        with self.assertLogs("recordings.readings", level="WARNING"):
            page = self.one(recording)
        self.assertEqual([(entry["sentence"], entry["found"] is None) for entry in page["items"] if entry["kind"] == "announced"],
                         [(EXAM, True), (HOMEWORK, True), (NO_CLASS, False)])
        with self.assertLogs("recordings.readings", level="WARNING"):
            self.assertEqual(self.listing()["attention"]["for_you"], 1)   # the day's plan is still read; a deleted term has no class meetings

    def test_the_assistant_proposes_the_move_again_only_on_a_card(self):
        rule = policy.base_rule("recordings.cards", {})
        self.assertEqual((rule.tier, rule.request_id), ("approve", True))


class AnnouncedTests(ClassBase):
    def test_a_day_told_to_the_class_that_disagrees_with_the_records_is_marked_with_both(self):
        self.plan(set=[{"kind": "homework", "title": "Homework 5.6", "sections": [], "date": FRI}])
        recording = self.taught()
        found = {entry["sentence"]: entry for entry in self.one(recording)["items"]}
        href = f"#course/{self.group}/section/507/schedule"
        # Exam 3's record says Thursday; the class was told Wednesday: both are said, and it needs the instructor.
        self.assertEqual((found[EXAM]["group"], found[EXAM]["when"], found[EXAM]["found"]["state"], found[EXAM]["found"]["href"]), ("you", "Wed, Nov 18", "differs", href))
        self.assertEqual(found[EXAM]["found"]["text"], "You told the class Wed, Nov 18. Faculty has Exam 3 given Thu, Nov 19 (its exam record).")
        self.assertEqual((found[EXAM]["found"]["said"], found[EXAM]["found"]["held"]), (NEXT_WED, [{"what": "given", "date": EXAM_DAY, "source": "exam"}]))
        # The planned homework is due the day that was said: nothing is asked.
        self.assertEqual((found[HOMEWORK]["group"], found[HOMEWORK]["found"]["state"], found[HOMEWORK]["found"]["text"]),
                         ("told", "agrees", "As Faculty has it: Homework 5.6 is due Fri, Nov 13 (the class schedule's plan)."))
        # The class schedule still has the class that was called off.
        self.assertEqual((found[NO_CLASS]["group"], found[NO_CLASS]["happening"], found[NO_CLASS]["found"]["text"]),
                         ("you", False, "You told the class there is no class Wed, Nov 25. The class schedule still has Section 507 meeting that day at 8:00 AM."))
        self.assertEqual((self.one(recording)["counts"], self.item("rec-class")["for_you"]), ({"you": 2, "covered": 2, "told": 1, "done": 0}, 2))
        # Nothing follows by itself: no card of any kind, and the records are as they were.
        self.assertEqual([card for card in self.cards() if card["engine"] == "recordings"], [])
        # Settled by the instructor, it no longer asks; corrected in the record, it agrees.
        self.assertEqual(self.post(f"{recording}/items/{found[NO_CLASS]['id']}", state="done").status_code, 200)
        self.exams.save_sitting(self.exam["id"], {"version": self.exams.get(self.exam["id"])["version"], "label": "Exam 3", "kind": "main", "date": NEXT_WED})
        found = {entry["sentence"]: entry for entry in self.one(recording)["items"]}
        self.assertEqual((found[NO_CLASS]["group"], found[EXAM]["group"], found[EXAM]["found"]["state"], self.item("rec-class")["for_you"]), ("done", "told", "agrees", 0))

    def test_a_day_of_something_faculty_holds_no_record_of_is_shown_and_nothing_is_claimed(self):
        self.engine.answer = lambda request: {"begins_at_line": 1, "items": [
            told("You told the class a review session is Friday.", "The homework for five six is due Friday.", 7, FRI),
            told("You told the class there is class on Thursday.", "Remember exam three is next Wednesday night.", 6, "2026-11-12", "class")]}
        recording = self.taught()
        found = {entry["sentence"]: entry for entry in self.one(recording)["items"]}
        self.assertEqual((found["You told the class a review session is Friday."]["group"], found["You told the class a review session is Friday."]["found"]), ("told", None))
        thursday = found["You told the class there is class on Thursday."]
        self.assertEqual((thursday["group"], thursday["found"]["text"]),
                         ("you", "You told the class it meets Thu, Nov 12. The class schedule has no class for Section 507 that day."))


class ComparisonTests(unittest.TestCase):
    def test_a_list_of_sections_reads_as_a_sentence_would(self):
        self.assertEqual([classes.listed(numbers) for numbers in (["5.6"], ["5.6", "5.7"], ["5.6", "5.7", "5.8"], [])],
                         ["5.6", "5.6 and 5.7", "5.6, 5.7 and 5.8", ""])


if __name__ == "__main__":
    unittest.main()
