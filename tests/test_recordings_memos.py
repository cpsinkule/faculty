"""Recordings, step 6: memos, training and search by meaning (docs/decisions/recordings.md, RECORDINGS "Memos, training, search by
meaning (step 6)"; ASSISTANT "A DICTATED REQUEST"; COURSE-NOTES "TRAINING RECORDINGS"; EMBEDDING "WHAT IS SENT") offline, against
the stand-in of the Plaud intake (tests/fake_intake.py), a SCRIPTED engine in the Assistant's place and a STAND-IN embedder: a memo
filed by the instructor is read into requests that rest on its exact words; each is put to the Assistant once, when Faculty is
sure the words are theirs, and every change that run wants waits as an Approve card; a training is kept under its course and
never read; and what was said is found by describing it, never from a recording that is Not Faculty's. No model, no Plaud,
no Google and no embedding worker is reached; the people and their words are invented."""
import sqlite3
import time
import unittest
import uuid
from unittest import mock

import numpy as np

from test_recordings import HEADERS, TUE, WED, campus
from test_recordings_page import PageBase
from test_recordings_reading import Engine, ReadingBase, answer as meeting_answer, said

from assistant import policy
from assistant import workflows as assistant_workflows
from faculty_embedding import client as embedding
from recordings import meaning, memos, reading, readings

THU = "2026-11-12"
# A memo, as Plaud would give it: one voice, which Plaud names as the instructor is named in its app.
MEMO = [
    said(0, "Chris", "Speaker 1", "Okay, memo to self while I walk to class."),
    said(4, "Chris", "Speaker 1", "Add office hours this Thursday from two to three in Blocker 220."),
    said(8, "Chris", "Speaker 1", "Also find out which of my students have not taken quiz four."),
    said(12, "Chris", "Speaker 1", "And from now on I do not accept late take-home assignments."),
]
HOURS, MISSING, RULE = ("Add office hours this Thursday from 2 to 3 PM in Blocker 220.", "Find out which students have not taken Quiz 4.",
                        "Late take-home assignments are not accepted.")


def memo_answer():
    """What the scripted engine answers for MEMO: two requests, a rule, and one item whose words were never said."""
    def item(kind, sentence, quote, line):
        return {"kind": kind, "sentence": sentence, "quote": quote, "line": line}
    return {"items": [
        item("todo", HOURS, "Add office hours this Thursday from two to three in Blocker 220.", 1),
        item("todo", MISSING, "find out which of my students have not taken quiz four.", 2),
        item("policy", RULE, "from now on I do not accept late take-home assignments.", 3),
        item("todo", "Email every student their grade.", "email every student their grade tonight", 2),
    ]}


class MemoEngine(Engine):
    """The Assistant's engine for the READING, scripted: a memo's instructions get the memo's answer, any other the meeting's."""
    memo = staticmethod(memo_answer)

    def submit(self, request):
        if request["prompt"] != reading.MEMO_PROMPT:
            return super().submit(request)
        self.asked.append(request)
        if self.error:
            raise self.error
        return {"state": "completed", "result": {"output": self.memo(), "metadata": {"requested_model": "scripted-model"}}}


class Turn:
    """The Assistant's engine for a RUN (a dictated request's preparation), scripted: one turn does what a test says."""
    name = "test"

    def __init__(self, callback):
        self.callback, self.asked = callback, []

    def turn(self, conversation, text, images=None):
        self.asked.append(text)
        self.callback(conversation, text)

    def stop(self):
        pass

    def close(self):
        pass


class MemoBase(ReadingBase):
    def setUp(self):
        super().setUp()
        self.engine = MemoEngine()
        self.engine.answer = meeting_answer
        self.assistant = self.app.extensions["assistant"]
        self.workflows = self.assistant.workflows
        patch = mock.patch.object(self.assistant.followups, "endpoint", return_value={"name": "Synthetic", "kind": "hosted", "model": "fake"})
        patch.start()
        self.addCleanup(patch.stop)

    def memo(self, plaud_id="rec-memo", transcript=MEMO, clock="20:00"):
        """A recording that fell in no calendar entry, filed as a memo by the instructor, kept and read."""
        self.fake.add(plaud_id, campus(WED, clock), minutes=1, title="Note", transcript=transcript)
        self.round()
        self.assertEqual(self.item(plaud_id)["filing"], "needs_filing")   # the calendar never files a memo
        self.assertEqual(self.file(plaud_id, kind="memo").status_code, 200)
        self.round()
        return self.item(plaud_id)["id"]

    def entries(self):
        """Faculty's own calendar entries on Thursday (the custom ones: what a card for "add office hours" writes)."""
        found = self.client.get("/api/calendar", query_string={"start": THU, "end": THU}).json["events"]
        return [(event["title"], event["date"], event["time"]) for event in found if event["title"] == "Office hours"]

    def requests(self, recording):
        return [entry for entry in self.one(recording)["items"] if entry["kind"] == "todo"]

    def runs_of(self):
        with self.db() as db:
            return [dict(row) for row in db.execute(assistant_workflows.LATEST + " WHERE r.kind='memo' ORDER BY r.rowid")]

    def run_with(self, callback):
        """One turn of the web process's dispatcher, with the Assistant's engine scripted. Returns what it was told."""
        engine = Turn(callback)
        with mock.patch("assistant.conversations.engine_for", return_value=engine):
            self.assertTrue(self.assistant.followups.dispatch_one())
            for _ in range(1000):
                if not self.workflows.busy():
                    return engine.asked
                time.sleep(.01)
        self.fail("The dictated request's run did not finish")

    @staticmethod
    def office_hours(conversation, text):
        """What a run does for "add office hours": one calendar entry (automatic in chat), and its answer."""
        made = conversation.service.write(conversation.session, "POST", "/api/calendar/events", {
            "title": "Office hours", "date": THU, "time": "14:00", "end_time": "15:00", "all_day": False, "request_id": str(uuid.uuid4())},
            "Add office hours on Thursday, Nov 12 from 2 to 3 PM, as your memo asked.")
        conversation.emit("assistant", text=f"I prepared the entry ({made['outcome']}). It waits for your approval.")


class MemoTests(MemoBase):
    def test_a_memo_is_read_into_requests_that_rest_on_its_exact_words(self):
        recording = self.memo()
        page = self.one(recording)
        self.assertEqual((page["meeting"], page["name"], page["reading"]["state"], page["reading"]["you"]), ("memo", "Memo", "read", "Chris"))
        self.assertEqual(page["memo"], {"sure": True, "why": "", "assistant": True})
        self.assertEqual([(entry["kind"], entry["sentence"], entry["group"], entry["for"]) for entry in page["items"]],
                         [("todo", HOURS, "asked", "you"), ("todo", MISSING, "asked", "you"), ("policy", RULE, "rules", "you")])
        hours = page["items"][0]
        # Who spoke and when come from the transcript; the quote is there word for word, and plays that moment.
        self.assertEqual((hours["quote"], hours["speaker"], hours["line"], hours["start_ms"], hours["at"]),
                         ("Add office hours this Thursday from two to three in Blocker 220.", "Chris", 1, 4000, "0:04"))
        self.assertEqual([(dropped["sentence"], dropped["reason"]) for dropped in page["reading"]["dropped"]],
                         [("Email every student their grade.", "its quote is not in the transcript at that line")])
        # ONE request for the reading: its own instructions, the memo's lines, and no record of Faculty's at all.
        (request,) = self.engine.asked
        self.assertEqual((request["prompt"], request["schema"], request["durable"]), (reading.MEMO_PROMPT, reading.MEMO_SCHEMA, False))
        self.assertEqual(request["payload"]["facts"], {"recorded": {"date": WED, "weekday": "Wednesday", "began": "20:00"},
                                                       "instructor": {"name": "Chris Example", "speaker": "Chris"}, "speakers": ["Chris"],
                                                       "courses": ["MATH 140"]})
        (run,) = self.runs()
        self.assertEqual((run["status"], run["version"], run["filed_as"]), ("read", "memo-v1", "memo"))
        # Each request was put to the Assistant, once, as a queued run of its own; the rule was not.
        self.assertEqual([(found["kind"], found["state"], found["request_id"]) for found in self.runs_of()],
                         [("memo", "queued", f"memo:auto:{entry['id']}") for entry in page["items"][:2]])
        self.assertEqual([entry["ask"]["state"] for entry in page["items"][:2]], ["queued", "queued"])
        self.assertNotIn("ask", page["items"][2])
        # While the Assistant has them, only the rule needs the instructor.
        self.assertEqual((page["counts"], self.item("rec-memo")["for_you"]), ({"you": 1, "asked": 2, "rules": 1, "done": 0}, 1))
        # A later round and a second look ask nothing again.
        self.round(later=700)
        self.assertEqual((len(self.engine.asked), len(self.runs_of())), (1, 2))

    def test_a_request_is_run_by_the_assistant_and_every_change_waits_as_a_card(self):
        recording = self.memo()
        (told,) = self.run_with(self.office_hours)
        # What the run is told: the memo's own instructions, the request, the exact words and the day it was dictated.
        self.assertIn("heard through a transcriber that mishears", told)
        self.assertIn("Add office hours this Thursday from two to three in Blocker 220.", told)
        self.assertRegex(told, r'"weekday":\s*"Wednesday"')
        self.assertNotIn("Trusted task", told)
        hours, missing = self.requests(recording)
        # The calendar entry is automatic in chat (assistant/policy.py); from a memo it is a card, and nothing was written.
        self.assertEqual(policy.base_rule("workspace_calendar.create", {}).tier, "auto")
        self.assertEqual((hours["ask"]["state"], hours["ask"]["answer"]), ("ready", "I prepared the entry (waiting_for_approval). It waits for your approval."))
        (card,) = hours["ask"]["cards"]
        self.assertEqual((card["pending"], card["endpoint"], card["page"]), (True, "workspace_calendar.create", None))
        self.assertEqual(self.entries(), [])
        self.assertEqual(missing["ask"]["state"], "queued")
        # Answered, the request needs the instructor (with the rule); the one still queued does not.
        self.assertEqual((self.one(recording)["counts"]["you"], self.item("rec-memo")["for_you"]), (2, 2))
        # Their Approve writes it, through the Calendar's own route.
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", headers=HEADERS, json={"seen": card["seen"]})
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(self.entries(), [("Office hours", THU, "14:00")])
        self.assertEqual(self.requests(recording)[0]["ask"]["cards"][0]["status"], "succeeded")
        # The second request has its own run, and a run that changes nothing leaves an answer and no card.
        self.run_with(lambda conversation, text: conversation.emit("assistant", text="Nobody is missing Quiz 4."))
        missing = self.requests(recording)[1]
        self.assertEqual((missing["ask"]["state"], missing["ask"]["answer"], missing["ask"]["cards"]), ("ready", "Nobody is missing Quiz 4.", []))
        self.assertFalse(self.assistant.followups.dispatch_one())   # nothing else is queued
        # Done settles a request; it no longer needs the instructor.
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}", state="done").status_code, 200)
        self.assertEqual(self.one(recording)["counts"], {"you": 2, "asked": 1, "rules": 1, "done": 1})

    def test_two_voices_ask_nothing_until_the_instructor_presses(self):
        heard = MEMO + [said(16, "Speaker 2", "Speaker 2", "Hey, are you coming to lunch?")]
        recording = self.memo(transcript=heard)
        page = self.one(recording)
        self.assertEqual((page["memo"]["sure"], page["reading"]["state"], len(page["items"])), (False, "read", 3))
        self.assertIn("Plaud heard 2 voices in this memo", page["memo"]["why"])
        self.assertEqual(self.runs_of(), [])
        hours = self.requests(recording)[0]
        self.assertEqual((hours["ask"]["state"], hours["ask"]["why"]), ("unsent", page["memo"]["why"]))
        self.assertEqual(self.item("rec-memo")["for_you"], 3)   # each waits on the instructor
        # Their own press puts that one request to the Assistant, and only that one; a repeated request ID asks once.
        pressed = self.post(f"{recording}/items/{hours['id']}/ask", request_id="ask-1")
        self.assertEqual(pressed.status_code, 200, pressed.json)
        self.assertEqual(self.post(f"{recording}/items/{hours['id']}/ask", request_id="ask-1").status_code, 200)
        self.assertEqual([(run["target"], run["state"], run["request_id"]) for run in self.runs_of()], [(hours["id"], "queued", "memo:ask-1")])
        self.assertEqual(self.post(f"{recording}/items/{hours['id']}/ask", request_id="ask-2").status_code, 409)   # already working on it
        self.assertEqual([entry["ask"]["state"] for entry in self.requests(recording)], ["queued", "unsent"])
        # A rule is not a request, and a meeting's item is not a memo's.
        rule = next(entry for entry in page["items"] if entry["kind"] == "policy")
        self.assertEqual(self.post(f"{recording}/items/{rule['id']}/ask").status_code, 400)

    def test_one_voice_named_as_somebody_else_is_not_sure_until_the_instructor_names_themself(self):
        other = [{**line, "speaker": "Dana Whitlock"} for line in MEMO]
        recording = self.memo(transcript=other)
        page = self.one(recording)
        self.assertEqual((page["memo"]["sure"], self.runs_of()), (False, []))
        self.assertIn("“Dana Whitlock”, which is not your name", page["memo"]["why"])
        # Named as the instructor in Faculty, the one voice is theirs: the requests go, without another reading.
        self.assertEqual(self.name(recording, "Speaker 1", "Chris Example").status_code, 200)
        self.assertEqual((self.one(recording)["memo"]["sure"], [run["state"] for run in self.runs_of()], len(self.engine.asked)),
                         (True, ["queued", "queued"], 1))
        # A voice Plaud left unnamed is taken as the instructor's too: the memo is theirs by their own filing.
        unnamed = [{**line, "speaker": "Speaker 1"} for line in MEMO]
        second = self.memo("rec-memo-2", transcript=unnamed, clock="21:00")
        self.assertEqual(self.one(second)["memo"], {"sure": True, "why": "", "assistant": True})
        self.assertEqual(memos.voice({"Speaker 1": "Speaker 1"}, ""), (True, ""))
        self.assertFalse(memos.voice({}, "")[0])
        # Named: every word of the voice's name is the instructor's. A name that only shares a word is somebody else's.
        own = "Chris Example"
        self.assertEqual([memos.voice({"Speaker 1": name}, own)[0] for name in ("Chris", "chris example", "Chris Wong", "Dr. Example", "Dana")],
                         [True, True, False, False, False])
        self.assertIn("Faculty does not know your name", memos.voice({"Speaker 1": "Chris"}, "")[1])

    def test_what_faculty_asked_by_itself_is_withdrawn_when_it_is_no_longer_sure_and_a_press_stands(self):
        recording = self.memo()
        hours, missing = self.requests(recording)
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}", state="dismissed").status_code, 200)
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}", state="reopened").status_code, 200)
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}/ask").status_code, 200)   # their own press
        self.assertEqual([(run["request_id"].startswith("memo:auto:"), run["state"]) for run in self.runs_of()],
                         [(True, "queued"), (True, "stale"), (False, "queued")])
        # The one voice is named as somebody else. Even before anything is withdrawn, the run Faculty queued by itself is
        # refused where it would start and where its cards would be approved (both read the request from Recordings) ...
        with mock.patch.object(self.record.reading, "voice_named"):
            self.assertEqual(self.name(recording, "Speaker 1", "Dana Whitlock").status_code, 200)
        with self.assertRaisesRegex(ValueError, "no longer sure"):
            self.workflows.observe("memo", hours["id"])
        self.assertEqual(self.workflows.observe("memo", missing["id"])["request"], MISSING)   # ... and what they pressed stands
        # ... and naming a speaker withdraws it: what Faculty asked by itself goes, what they pressed stays queued.
        self.record.reading.voice_named(recording)
        self.assertEqual([run["state"] for run in self.runs_of()], ["stale", "stale", "queued"])
        states = {entry["id"]: entry["ask"] for entry in self.requests(recording)}
        self.assertEqual((states[hours["id"]]["state"], states[hours["id"]]["answer"], states[missing["id"]]["state"]), ("stale", memos.NOT_SURE, "queued"))
        # Withdrawn, the request can still be put to the Assistant by their own press.
        self.assertEqual(self.post(f"{recording}/items/{hours['id']}/ask").status_code, 200)
        self.assertEqual(self.requests(recording)[0]["ask"]["state"], "queued")

    def test_with_the_assistant_off_a_queued_request_says_so_and_its_run_is_listed_with_its_memos_page(self):
        recording = self.memo()
        self.record.reading.connection = lambda: None   # Compute settings name no model for the Assistant now
        page = self.one(recording)
        self.assertEqual((page["memo"]["assistant"], [entry["ask"]["why"] for entry in page["items"][:2]]), (False, [memos.OFF, memos.OFF]))
        # The Assistant's own list of its runs names the memo's page, at the request's words.
        listed = [run for run in self.workflows.listing()["preparations"] if run["kind"] == "memo"]
        self.assertEqual(sorted(run["page"] for run in listed), sorted([f"#recordings/{recording}?t=4000", f"#recordings/{recording}?t=8000"]))

    def test_a_dismissed_request_has_its_run_and_cards_withdrawn(self):
        recording = self.memo()
        self.run_with(self.office_hours)
        hours, missing = self.requests(recording)
        (card,) = hours["ask"]["cards"]
        # Dismissed: that was not what they asked. The card is taken back and can no longer be approved.
        self.assertEqual(self.post(f"{recording}/items/{hours['id']}", state="dismissed").status_code, 200)
        self.assertEqual([found["status"] for found in self.one(recording)["items"][0]["ask"]["cards"]], ["expired"])
        self.assertEqual(self.one(recording)["items"][0]["group"], "done")
        refused = self.client.post(f"/api/assistant/actions/{card['id']}/approve", headers=HEADERS, json={"seen": card["seen"]})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.entries(), [])
        # One dismissed before its run never runs.
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}", state="dismissed").status_code, 200)
        self.assertEqual([run["state"] for run in self.runs_of()], ["stale", "stale"])
        self.assertFalse(self.assistant.followups.dispatch_one())
        # Opened again it is asked only by their press (Faculty's own ask is once), and a settled one refuses the press.
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}/ask").status_code, 409)
        self.assertEqual(self.post(f"{recording}/items/{missing['id']}", state="reopened").status_code, 200)
        self.assertEqual(self.requests(recording)[1]["ask"]["state"], "stale")
        again = self.post(f"{recording}/items/{missing['id']}/ask", guidance="Only section 508.")
        self.assertEqual(again.status_code, 200, again.json)
        (told,) = self.run_with(lambda conversation, text: conversation.emit("assistant", text="Two students in 508."))
        self.assertIn("Only section 508.", told)
        self.assertEqual(self.requests(recording)[1]["ask"]["answer"], "Two students in 508.")

    def test_filed_elsewhere_a_memos_runs_are_withdrawn_and_a_reread_asks_only_what_is_new(self):
        recording = self.memo()
        self.run_with(self.office_hours)
        # Read again: the same requests keep their runs (the same words are the same request); a new one is asked.
        first = memo_answer()["items"]
        self.engine.memo = lambda: {"items": first[:3] + [{"kind": "todo", "sentence": "Note the walk to class.",
                                                            "quote": "memo to self while I walk to class.", "line": 0}]}
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([entry["ask"]["state"] for entry in self.requests(recording)], ["ready", "queued", "queued"])
        self.assertEqual(len(self.runs_of()), 3)
        (card,) = self.requests(recording)[0]["ask"]["cards"]
        self.assertTrue(card["pending"])   # the card of a request the new reading still holds stands
        # Filed under the term instead: it is no memo, so every run and waiting card is withdrawn; nothing runs.
        self.assertEqual(self.file("rec-memo", kind="term", term_id=self.term).status_code, 200)
        self.assertEqual({run["state"] for run in self.runs_of()}, {"stale"})
        self.assertEqual(self.cards(), [])
        self.assertFalse(self.assistant.followups.dispatch_one())
        with self.assertRaisesRegex(ValueError, "no longer a request of a memo"):
            self.record.reading.request(self.runs_of()[0]["target"])

    def test_the_assistant_cannot_send_itself_a_request_and_a_memo_run_starts_no_other(self):
        recording = self.memo()
        self.assertEqual(policy.base_rule("recordings.ask", {}).tier, "never")
        hours = self.requests(recording)[0]
        outcomes = []

        def tries(conversation, text):
            write = lambda path, body: conversation.service.write(conversation.session, "POST", path, body, "Trying.")  # noqa: E731
            outcomes.append(write(f"/api/recordings/{recording}/items/{hours['id']}/ask", {"request_id": str(uuid.uuid4())})["outcome"])
            try:
                write("/api/assistant/preparations", {"kind": "reconciliation", "target": self.term, "request_id": str(uuid.uuid4())})
            except ValueError as refused:
                outcomes.append(str(refused))
            conversation.emit("assistant", text="Nothing done.")
        self.run_with(tries)
        self.assertEqual(outcomes, ["refused", "ASSISTANT: background work cannot start another preparation."])
        # A dictated request is queued by Recordings alone: the Assistant's own preparation route does not take the kind.
        made = self.client.post("/api/assistant/preparations", headers=HEADERS, json={"kind": "memo", "target": hours["id"], "request_id": "p-1"})
        self.assertEqual(made.status_code, 400)

    def test_past_the_first_few_requests_wait_for_the_press(self):
        lines = [said(4 * n, "Chris", "Speaker 1", f"Please look up record number {n} for me today.") for n in range(8)]
        self.engine.memo = lambda: {"items": [{"kind": "todo", "sentence": f"Look up record {n}.", "quote": f"look up record number {n} for me today.", "line": n}
                                              for n in range(8)]}
        recording = self.memo(transcript=lines)
        states = [entry["ask"] for entry in self.requests(recording)]
        self.assertEqual([ask["state"] for ask in states], ["queued"] * memos.ASKS + ["unsent"] * 2)
        self.assertEqual(states[-1]["why"], memos.MORE)


class TrainingTests(ReadingBase):
    def test_a_training_is_filed_under_its_course_kept_and_listed_and_never_read(self):
        self.fake.add("rec-training", campus(WED, "13:00"), minutes=50, title="Grader training", transcript=[
            said(0, "Avery Quill", "Speaker 1", "Every grader marks the rubric line before writing a comment.")])
        self.round()
        self.assertEqual(self.file("rec-training", kind="training", definition_id=self.definition).status_code, 200)
        self.round()
        row = self.item("rec-training")
        self.assertEqual((row["name"], row["filing"], row["kept"], row["for_you"]), ("Training · MATH 140", "filed", ["transcript"], 0))
        self.assertEqual(row["links"][0], {"kind": "training", "label": "Training · MATH 140", "filed_by": "instructor", "appointment": False,
                                           "definition_id": self.definition})
        (link,) = self.links("rec-training")
        self.assertEqual((link["kind"], link["owner_key"], link["purpose"]), ("course", f"course:{self.definition}", "training"))
        # Kept for reference: never read for items, and its page says so.
        self.assertEqual(self.engine.asked, [])
        page = self.one(row["id"])
        self.assertEqual((page["meeting"], page["reading"]["state"], page["reading"]["detail"], page["items"]), ("training", "not_read", readings.TRAINING, []))
        # The course's page lists it; another course has none; its words are found like any kept recording's.
        listed = self.client.get(f"/api/recordings/trainings?definition_id={self.definition}").json
        self.assertEqual((listed["count"], listed["code"], listed["recordings"]),
                         (1, "MATH 140", [{"id": row["id"], "date": WED, "time": "13:00", "start_known": True, "duration_ms": 50 * 60000,
                                           "kept": ["transcript"]}]))
        self.assertEqual(self.client.get("/api/recordings/trainings?definition_id=nope").status_code, 404)
        # Asked for by its course, the list is that course's trainings alone, every one of them.
        self.meeting()
        self.round()
        self.assertEqual(len(self.listing()["recordings"]), 2)
        only = self.client.get(f"/api/recordings?course={self.definition}").json
        self.assertEqual(([found["name"] for found in only["recordings"]], only["choices"]["courses"]),
                         (["Training · MATH 140"], [{"id": self.definition, "label": "MATH 140"}]))
        self.assertEqual(self.client.get("/api/recordings?course=nope").json["recordings"], [])
        self.assertEqual([hit["name"] for hit in self.search("rubric line")["results"]], ["Training · MATH 140"])
        # The same course as a MEETING is the same owner with another purpose: one more revision of that link, and it is read.
        self.assertEqual(self.file("rec-training", kind="course", definition_id=self.definition).status_code, 200)
        (link,) = self.links("rec-training")
        self.assertEqual((link["revision"], link["purpose"], self.item("rec-training")["name"]), (2, "", "MATH 140"))
        self.assertEqual(self.client.get(f"/api/recordings/trainings?definition_id={self.definition}").json["count"], 0)
        self.round()
        self.assertEqual(len(self.engine.asked), 2)   # the term's meeting above, and now this one
        # Filing it as a training again is a change, not a repeat: its reading is no longer shown.
        self.assertEqual(self.file("rec-training", kind="training", definition_id=self.definition).status_code, 200)
        self.assertEqual((self.links("rec-training")[0]["revision"], self.one(row["id"])["reading"]["state"]), (3, "not_read"))
        self.assertEqual(self.file("rec-training", kind="training").status_code, 400)   # a training is some course's

    def test_only_a_course_link_is_a_training(self):
        with self.db() as db, self.assertRaisesRegex(sqlite3.IntegrityError, "Only a recording filed under a course is a training"):
            db.execute("INSERT INTO recordings(id, plaud_id, captured_at) VALUES ('r1','p1','2026-11-11T00:00:00Z')")
            db.execute("""INSERT INTO recording_links(id, recording_id, kind, owner_key, revision, state, term_id, filed_by, actor, created_at, purpose)
                          VALUES ('l1','r1','term',?,1,'filed',?,'instructor','workspace','2026-11-11T00:00:00Z','training')""",
                       (f"term:{self.term}", self.term))


AXES = {"sit": 0, "seating": 0, "charts": 0, "calculators": 1, "calculator": 1, "device": 1, "makeup": 2, "room": 2}


class StandIn:
    """The embedding worker, stood in for: a text lands on the axis of the first topic word it holds (so "where students sit"
    is near "the seating charts" though they share no word), else on an axis of its own. It remembers what it was sent."""

    def __init__(self):
        self.sent, self.down, self.lent, self.refuse = [], False, False, ""

    def text_vectors(self, texts, role="query", timeout=1.5, path=None):
        if self.lent:
            raise embedding.CardLent("The GPU is lent.")
        if self.down:
            raise embedding.EmbeddingUnavailable("The embedding worker did not answer: timeout")
        if self.refuse and any(self.refuse in text for text in texts):
            raise embedding.EmbeddingUnavailable("That text was refused.")
        self.sent += [(role, text) for text in texts]
        rows = np.zeros((len(texts), 16), dtype=np.float32)
        for row, text in enumerate(texts):
            words = [word.strip(".,?").casefold() for word in text.split()]
            rows[row, next((AXES[word] for word in words if word in AXES), 15)] = 1
        return rows

    def status(self, timeout=1.5, path=None):
        if self.down:
            raise embedding.EmbeddingUnavailable("down")
        return {"lent": self.lent}


class MeaningTests(PageBase):
    def setUp(self):
        super().setUp()
        self.worker = StandIn()
        for name, value in (("configured", lambda: True), ("text_vectors", self.worker.text_vectors), ("status", self.worker.status)):
            patch = mock.patch.object(embedding, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(meaning, "PASSAGE", 30)   # the fixture's three short lines are three passages
        patch.start()
        self.addCleanup(patch.stop)
        self.index = self.record.meaning

    def turn(self):
        self.index._next = 0
        return self.index.turn()

    def test_what_was_said_is_found_by_describing_it(self):
        recording = self.kept()
        self.assertEqual(self.turn(), 3)
        self.assertEqual(self.turn(), 0)   # a kept transcript never changes: it is embedded once
        # Each passage went as one text, as a document, words alone: no speaker, no time, no name of a recording.
        self.assertEqual(self.worker.sent, [("document", "Calculators stay in the bag for Exam 3."),
                                            ("document", "And the seating charts go out Thursday, 100% of them."),
                                            ("document", "Good. The makeup room is BLOC 166.")])
        found = self.search("where students sit", meaning=True)
        self.assertEqual((found["results"], found["meaning"]), ([], {"state": "ok", "detail": "", "indexed": 3, "waiting": 0}))
        first = found["nearest"][0]
        self.assertEqual((first["recording_id"], first["name"], first["date"], first["line"], first["at"], first["href"]),
                         (recording, "MATH 140 · Fall 2026", TUE, 1, "0:03", f"#recordings/{recording}?t=3200"))
        self.assertEqual(first["lines"], [{"speaker": "Speaker 2", "text": "And the seating charts go out Thursday, 100% of them.", "start_ms": 3200, "at": "0:03"}])
        self.assertEqual(self.worker.sent[-1], ("query", "where students sit"))
        # A passage a keyword result already shows is not repeated among the nearest.
        both = self.search("seating", meaning=True)
        self.assertEqual(([hit["line"] for hit in both["results"]], [hit["line"] for hit in both["nearest"]][:1] == [1]), ([1], False))
        # Asked without `meaning`, the answer is what it always was.
        self.assertEqual(sorted(self.search("seating")), ["more", "query", "results", "words"])
        # One recording alone, and a speaker named in Faculty reads by that name.
        self.assertEqual(self.name(recording, "Speaker 2", "Avery Quill").status_code, 200)
        self.assertEqual(self.search("where students sit", meaning=True, recording_id=recording)["nearest"][0]["lines"][0]["speaker"], "Avery Quill")
        self.assertEqual(self.search("where students sit", meaning=True, recording_id="another")["nearest"], [])
        # The index holds no word that was said: only which lines, a checksum and numbers.
        folder = self.index.index.path.parent
        held = b"".join(path.read_bytes() for path in folder.iterdir() if path.name.startswith("passages.sqlite3"))   # its WAL too
        with sqlite3.connect(self.index.index.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*), MIN(LENGTH(vector)) FROM passages").fetchone(), (3, 64))
        self.assertGreater(len(held), 4096)
        for word in (b"seating", b"Calculators", b"makeup", b"Speaker", b"Chris", b"MATH"):
            self.assertNotIn(word, held)

    def test_a_recording_that_is_not_kept_never_surfaces(self):
        self.kept()
        # One that waits for Keep or Don't keep was never fetched: it has no transcript, so nothing of it is read or sent.
        self.fake.add("rec-waiting", campus(TUE, "18:30"), transcript=[said(0, "Chris", "Speaker 1", "The seating charts are a secret.")])
        self.round()
        self.assertEqual(self.item("rec-waiting")["filing"], "needs_filing")
        self.assertEqual(self.turn(), 3)
        self.assertNotIn("secret", " ".join(text for _, text in self.worker.sent))
        self.assertEqual(len(self.search("where students sit", meaning=True)["nearest"]), 3)
        # Don't keep, of one that was kept, pressed while a round is running (so it is hidden, and not erased yet): at once it
        # is not shown, though its vectors are still in the index ...
        with self.record.lock():
            self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertTrue(self.held("rec-meeting"))
        self.assertEqual(self.index.index.count(meaning.MODEL), 3)
        found = self.search("where students sit", meaning=True)
        self.assertEqual((found["nearest"], found["meaning"]["waiting"]), ([], 0))
        # ... and the worker's next turn drops them: the cache keeps nothing it may not show.
        self.assertEqual((self.turn(), self.index.index.count(meaning.MODEL)), (0, 0))
        # Erased by the next round, it leaves nothing for a later turn to find.
        self.round()
        self.assertFalse(self.held("rec-meeting"))
        self.assertEqual((self.turn(), self.index.index.count(meaning.MODEL)), (0, 0))
        # The same for one only partly read when it was erased at the press: kept, read in part, then Don't keep.
        self.assertEqual(self.file("rec-waiting", kind="term", term_id=self.term).status_code, 200)
        self.round()
        with mock.patch.object(meaning, "BATCH", 1):
            self.assertEqual((self.turn(), self.index.index.count(meaning.MODEL)), (1, 1))
        self.assertEqual(self.discard("rec-waiting").status_code, 200)
        self.assertFalse(self.held("rec-waiting"))
        self.assertEqual((self.turn(), self.index.index.count(meaning.MODEL)), (0, 0))
        with sqlite3.connect(self.index.index.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM passages").fetchone()[0], 0)
        # Asked about again and kept, it is fetched again and read again.
        self.assertEqual(self.client.post("/api/recordings/ask-again", headers=HEADERS, json={"request_id": str(uuid.uuid4())}).json["asked"], 2)
        self.record.run_due()
        self.assertEqual(self.file("rec-meeting", kind="term", term_id=self.term).status_code, 200)
        self.record.run_due()
        self.assertEqual((self.turn(), [hit["recording_id"] for hit in self.search("where students sit", meaning=True)["nearest"]][:1]),
                         (3, [self.item("rec-meeting")["id"]]))

    def test_a_worker_that_is_off_down_or_lending_fails_nothing(self):
        self.kept()
        # Lending the GPU, or silent: the turn waits and blames no passage.
        self.worker.lent = True
        self.assertEqual(self.turn(), 0)
        self.worker.lent, self.worker.down = False, True
        for _ in range(5):
            self.assertEqual(self.turn(), 0)
        with sqlite3.connect(self.index.index.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM skipped").fetchone()[0], 0)
        found = self.search("where students sit", meaning=True)
        self.assertEqual((found["nearest"], found["meaning"]["state"], found["meaning"]["detail"], [hit["line"] for hit in self.search("seating", meaning=True)["results"]]),
                         ([], "unavailable", meaning.SILENT, [1]))
        # Up again, and refusing one passage itself: after three tries that one is set aside and the rest are embedded.
        self.worker.down, self.worker.refuse = False, "makeup room"
        self.assertEqual([self.turn() for _ in range(3)], [2, 0, 0])
        with sqlite3.connect(self.index.index.path) as db:
            self.assertEqual(db.execute("SELECT first, reason FROM skipped").fetchall(), [(2, "That text was refused.")])
        self.assertEqual(self.search("where students sit", meaning=True)["meaning"]["indexed"], 2)
        # With no worker named (as every check and a second copy of the app runs), nothing is sent and the search says so.
        with mock.patch.object(embedding, "configured", lambda: False):
            sent = len(self.worker.sent)
            off = self.search("where students sit", meaning=True)
            self.assertEqual((self.turn(), off["nearest"], off["meaning"]["state"], off["meaning"]["detail"], len(self.worker.sent)),
                             (0, [], "off", meaning.OFF, sent))

    def test_passages_are_gathered_in_order_and_bounded(self):
        lines = [{"position": n, "start_ms": n * 1000, "end_ms": n * 1000 + 900, "speaker": "A", "speaker_key": "A", "text": f"word{n} " * 9} for n in range(40)]
        with mock.patch.object(meaning, "PASSAGE", 1000):
            found = meaning.gathered(lines)
        self.assertEqual([(passage["first"], passage["last"]) for passage in found][:2], [(0, 17), (18, 33)])
        self.assertTrue(all(len(passage["text"]) <= meaning.TEXT for passage in found))
        self.assertEqual(meaning.gathered([{**lines[0], "text": "Okay then."}]), [])   # too few words to be found by meaning


if __name__ == "__main__":
    unittest.main()
