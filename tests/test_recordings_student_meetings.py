"""Recordings, step 4: a student's recorded meeting (docs/decisions/recordings.md, RECORDINGS "Student meetings (step 4)",
"SEVERAL STUDENTS IN ONE RECORDING", "AN APPOINTMENT IS SETTLED ALREADY"; COMMUNICATIONS "FACULTY'S OWN RECORDS";
TEACHING-WORKFLOWS "SEPARATE COMMITMENTS"; STUDENT-NOTES; ASSISTANT "FOLLOW-UP FACTS").

Against tests/fake_intake.py and a SCRIPTED engine in the Assistant's place: no check asks a model, reaches Plaud or reads a real
recording. Everyone here is invented.
"""
import json
import unittest
import uuid

from test_recordings import ALICE, BOB, HEADERS, TUE, campus
from test_recordings_reading import NEXT_TUE, ReadingBase, item, said
import test_student_support

from assistant import policy
from recordings import filing, meetings, reading

# A student's visit, as Plaud would give it (the shape of its own transcripts; nobody here is real).
VISIT = [
    said(0, "Chris", "Speaker 1", "Come in, have a seat."),
    said(4, "Alice", "Speaker 2", "I missed Quiz 5 because I was sick on Thursday."),
    said(8, "Chris", "Speaker 1", "Email me the doctor's note and we will see about a makeup."),
    said(12, "Alice", "Speaker 2", "I will send the note tonight."),
    said(16, "Chris", "Speaker 1", "I will regrade question two on your Exam 2 this week."),
    said(20, "Chris", "Speaker 1", "Come back next Tuesday at two and we will go over it."),
    said(24, "Chris", "Speaker 1", "From now on nobody gets a makeup without a note."),
    said(28, "Bob", "Speaker 3", "Hi, do you have a minute for my homework question?"),
    said(32, "Chris", "Speaker 1", "Sure. I will post the worked solution for you tonight."),
]
SICK, NOTE = "They said they missed Quiz 5 because they were sick on Thursday.", "They will send the doctor's note tonight."
REGRADE, BACK = "Regrade question 2 on their Exam 2 this week.", "They come back next Tuesday at 2 pm to go over it."
POSTED = "Post the worked solution for them tonight."


def visit(exam="a1"):
    """What the scripted engine answers for VISIT: six items, of which a student's meeting keeps five."""
    return {"begins_at_line": 1, "items": [
        item("answer", SICK, "I missed Quiz 5 because I was sick on Thursday.", 1),
        item("waiting", NOTE, "I will send the note tonight.", 3, **{"for": "others"}),
        item("todo", REGRADE, "I will regrade question two on your Exam 2 this week.", 4),
        item("date", BACK, "Come back next Tuesday at two and we will go over it.", 5, date=NEXT_TUE, time="14:00", date_words="next Tuesday at two"),
        # One student's meeting sets no rule for the course: never a standing rule from it.
        item("policy", "Nobody gets a makeup without a note.", "From now on nobody gets a makeup without a note.", 6),
        item("todo", POSTED, "I will post the worked solution for you tonight.", 8),
    ]}


class StudentBase(ReadingBase):
    def setUp(self):
        super().setUp()
        self.engine.answer = visit
        self.alice, self.bob = self.students[ALICE], self.students[BOB]

    def visit(self, plaud_id="rec-visit", clock="14:02", book=True, **more):
        """A recording that began inside Alice's appointment: filed under her, kept and read in the same round."""
        self.booked = self.appointment(ALICE, TUE, "14:00", "14:30") if book else None
        self.fake.add(plaud_id, campus(TUE, clock), minutes=12, title="Office hours", **{"transcript": VISIT, **more})
        self.assertEqual(self.round()["status"], "ok")
        return self.item(plaud_id)["id"]

    def mine(self):
        return [card for card in self.cards() if card["engine"] == "recordings"]

    def card(self, endpoint):
        (found,) = [card for card in self.mine() if card["endpoint"].startswith(endpoint)]
        return found

    def approve(self, card):
        return self.client.post(f"/api/assistant/actions/{card['id']}/approve", headers=HEADERS, json={"seen": card["seen"]})

    def followups(self, enrollment):
        answer = self.client.get("/api/student-support", query_string={"kind": "followup", "enrollment_id": enrollment})
        self.assertEqual(answer.status_code, 200, answer.json)
        return [entry for entry in answer.json["items"] if entry["id"]]

    def timeline(self, enrollment):
        answer = self.client.get(f"/api/communications/students/{enrollment}")
        self.assertEqual(answer.status_code, 200, answer.json)
        return [entry for entry in answer.json["entries"] if entry["channel"] == "meeting"]

    def notes(self, enrollment):
        return self.client.get(f"/api/student-notes/students/{enrollment}").json["notes"]

    def parts(self, recording, *students, request=None):
        return self.client.post(f"/api/recordings/{recording}/students", headers=HEADERS, json={
            "request_id": request or str(uuid.uuid4()),
            "students": [{"enrollment_id": enrollment, "start_ms": start, "end_ms": end} for enrollment, start, end in students]})


class ReadingTests(StudentBase):
    def test_a_students_meeting_is_read_by_its_own_instructions_for_what_was_promised(self):
        recording = self.visit()
        (request,) = self.engine.asked
        self.assertEqual((request["prompt"], request["schema"], request["durable"]), (reading.STUDENT_PROMPT, reading.SCHEMA, False))
        facts = request["payload"]["facts"]
        # Who the meeting was with, each read through its owner: the roster's name and section, the appointment it was. Nothing
        # of the coordinator or the Canvas copy, and no grade, note, absence or message of the student's.
        self.assertEqual(facts["students"], [{"name": "Example, Alice", "section": 507, "appointment": {
            "date": TUE, "time": "14:00", "end_time": "14:30", "purpose": "Exam 2 questions", "recorded_as": "scheduled"}}])
        self.assertTrue({"coordinator", "canvas"}.isdisjoint(facts))
        self.assertEqual(set(facts), {"recorded", "instructor", "speakers", "course", "term", "sections", "assessments", "calendar", "students"})
        (run,) = self.runs()
        self.assertEqual((run["status"], run["version"], run["filed_as"]), ("read", reading.STUDENT_VERSION, f"enrollment:{self.alice}"))
        page = self.one(recording)
        self.assertEqual((page["meeting"], page["reading"]["state"]), ("student", "read"))
        self.assertEqual({entry["sentence"]: (entry["kind"], entry["group"], entry["enrollment_id"], entry["found"]) for entry in page["items"]}, {
            SICK: ("answer", "answers", self.alice, None), NOTE: ("waiting", "others", self.alice, None),
            REGRADE: ("todo", "you", self.alice, None), BACK: ("date", "you", self.alice, None), POSTED: ("todo", "you", self.alice, None)})
        self.assertEqual([(dropped["sentence"], dropped["reason"]) for dropped in page["reading"]["dropped"]],
                         [("Nobody gets a makeup without a note.", "not a kind of item this meeting is read for")])
        (student,) = page["students"]
        self.assertEqual((student["name"], student["sections"], student["part"], student["off"], student["items"], student["on_followup"]),
                         ("Example, Alice", [507], None, "", 5, 0))
        self.assertEqual(student["appointment"], {"state": "scheduled", "date": TUE, "time": "14:00", "end_time": "14:30"})
        # The list counts what she is owed, and a meeting filed under a term is read as before.
        self.assertEqual(self.item("rec-visit")["for_you"], 3)

    def test_filed_by_hand_under_a_student_it_is_read_the_same_with_no_appointment(self):
        recording = self.visit(book=False)
        self.assertEqual(self.item("rec-visit")["filing"], "needs_filing")
        self.assertEqual(self.engine.asked, [])
        self.assertEqual(self.file("rec-visit", kind="student", enrollment_id=self.alice).status_code, 200)
        self.round(later=30)
        (request,) = self.engine.asked
        self.assertEqual(request["payload"]["facts"]["students"], [{"name": "Example, Alice", "section": 507}])
        self.assertEqual(self.one(recording)["students"][0]["appointment"], None)

    def test_the_assistant_proposes_the_students_cards_and_parts_only_on_a_card(self):
        for endpoint in ("recordings.cards", "recordings.students"):
            rule = policy.base_rule(endpoint, {})
            self.assertEqual((rule.tier, rule.request_id), ("approve", True), endpoint)


class TimelineTests(StudentBase):
    def test_it_is_on_that_students_timeline_by_reference_and_nowhere_else(self):
        recording = self.visit()
        (entry,) = self.timeline(self.alice)
        self.assertEqual({key: entry[key] for key in ("key", "channel", "channel_label", "subject", "author_role", "attachments", "recipients", "roles", "original")},
                         {"key": f"meeting:{recording}", "channel": "meeting", "channel_label": "Meeting", "subject": "Recorded meeting · 12 min",
                          "author_role": "meeting", "attachments": [], "recipients": [], "roles": ["about"], "original": False})
        self.assertEqual(entry["at"], "2026-11-10T20:02:00.000Z")   # when it began: 2:02 pm on campus
        self.assertEqual(entry["context"], {"source": "Recorded meeting", "recording_id": recording, "href": f"#recordings/{recording}", "appointment": True})
        self.assertEqual(entry["text"], "\n".join((
            "You said you would:", f"- {REGRADE}", f"- {POSTED}", "They said they would:", f"- {NOTE}",
            "Days that were set:", f"- {BACK} (Tue, Nov 17 · 2:00 PM)", "They told you:", f"- {SICK}")))
        self.assertNotIn("message_id", entry)   # no file, nothing to download, nothing a student wrote
        self.assertEqual(self.timeline(self.bob), [])
        # A dismissed item is the instructor saying it was not so: it leaves her timeline.
        sick = next(found for found in self.one(recording)["items"] if found["sentence"] == SICK)
        self.assertEqual(self.post(f"{recording}/items/{sick['id']}", state="dismissed").status_code, 200)
        self.assertNotIn("They told you:", self.timeline(self.alice)[0]["text"])
        # Filed elsewhere, it is no longer her meeting.
        self.assertEqual(self.file("rec-visit", kind="memo").status_code, 200)
        self.assertEqual(self.timeline(self.alice), [])

    def test_a_follow_up_preparation_is_given_her_latest_meeting_items_and_no_other_students(self):
        self.visit()
        followups = self.app.extensions["assistant"].followups
        with self.db() as db:
            lines = followups._recorded_facts(db, {"enrollment_id": self.alice})
            self.assertEqual(followups._recorded_facts(db, {"enrollment_id": self.bob}), [])
        self.assertIn("A reply to this student may refer to what was said in their own meeting; never to another student's meeting", lines[0])
        self.assertEqual(lines[1:], [
            f"- Tue, Nov 10, they told you: {SICK} [the words: “I missed Quiz 5 because I was sick on Thursday.”]",
            f"- Tue, Nov 10, they said they would: {NOTE} [the words: “I will send the note tonight.”]",
            f"- Tue, Nov 10, you said you would: {REGRADE} [the words: “I will regrade question two on your Exam 2 this week.”]",
            f"- Tue, Nov 10, a day that was set: {BACK} (Tue, Nov 17 · 2:00 PM) [the words: “Come back next Tuesday at two and we will go over it.”]",
            f"- Tue, Nov 10, you said you would: {POSTED} [the words: “I will post the worked solution for you tonight.”]"])
        with self.db() as db:
            self.assertEqual(len(meetings.facts(db, self.alice, most=2)), 3)   # at most so many items, under the one heading


class CardTests(StudentBase):
    def test_what_was_promised_is_one_card_that_opens_a_follow_up_only_on_approve(self):
        recording = self.visit()
        card = self.card("student_support")
        self.assertEqual((card["endpoint"], card["method"], card["path"], card["tier"], card["page"]),
                         ("student_support.create", "POST", "/api/student-support", "approve", f"#recordings/{recording}?t=12000"))
        ids = {entry["sentence"]: entry["id"] for entry in self.one(recording)["items"]}
        body = card["body"]
        self.assertEqual({key: body[key] for key in ("kind", "enrollment_id", "title", "state", "details", "date")},
                         {"kind": "followup", "enrollment_id": self.alice, "title": "Meeting on Tue, Nov 10", "state": "todo", "details": "", "date": ""})
        self.assertEqual(body["commitments"], [
            {"id": f"rec-{ids[NOTE]}", "title": NOTE, "kind": "work", "state": "open", "waiting_on": "student", "recording_item_id": ids[NOTE]},
            {"id": f"rec-{ids[REGRADE]}", "title": REGRADE, "kind": "work", "state": "open", "waiting_on": "you", "recording_item_id": ids[REGRADE]},
            {"id": f"rec-{ids[BACK]}", "title": BACK, "kind": "check_back", "state": "open", "waiting_on": "date", "check_back": NEXT_TUE,
             "recording_item_id": ids[BACK]},
            {"id": f"rec-{ids[POSTED]}", "title": POSTED, "kind": "work", "state": "open", "waiting_on": "you", "recording_item_id": ids[POSTED]}])
        self.assertEqual(card["summary"], "Track 4 things said in your meeting with Example, Alice on Tue, Nov 10 on a new follow-up.")
        self.assertEqual(self.followups(self.alice), [])   # nothing is tracked until Approve
        # Read again: the same promises keep their card; no second one is made.
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([found["id"] for found in self.mine() if found["endpoint"] == "student_support.create"], [card["id"]])
        approved = self.approve(card)
        self.assertEqual((approved.status_code, approved.json["action"]["status"]), (200, "succeeded"), approved.json)
        (followup,) = self.followups(self.alice)
        self.assertEqual((followup["title"], followup["saved_state"], len(followup["commitments"])), ("Meeting on Tue, Nov 10", "todo", 4))
        heard = followup["commitments"][1]["heard"]
        self.assertEqual(heard, {"recording_id": recording, "start_ms": 16000, "day": TUE, "href": f"#recordings/{recording}?t=16000"})
        # What her follow-up holds is no longer asked of the instructor here, and is not proposed again.
        page = self.one(recording)
        self.assertEqual({entry["sentence"]: entry["group"] for entry in page["items"]},
                         {SICK: "answers", NOTE: "done", REGRADE: "done", BACK: "done", POSTED: "done"})
        self.assertEqual((page["students"][0]["on_followup"], page["students"][0]["offer"]["commitments"], self.item("rec-visit")["for_you"]), (4, False, 0))
        self.assertEqual(self.post(f"{recording}/cards", kind="commitments").status_code, 409)
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([found for found in self.mine() if found["endpoint"].startswith("student_support")], [])

    def test_an_open_follow_up_gets_them_after_its_own_and_a_changed_one_refuses_the_approve(self):
        own = {"id": "own", "title": "Check her Exam 1 total", "kind": "work", "state": "open", "waiting_on": "you"}
        made = self.client.post("/api/student-support", headers=HEADERS, json={
            "request_id": "open-1", "kind": "followup", "enrollment_id": self.alice, "title": "Exam 1 total", "state": "todo", "commitments": [own]})
        self.assertEqual(made.status_code, 201, made.json)
        recording = self.visit()
        card = self.card("student_support")
        self.assertEqual((card["endpoint"], card["path"]), ("student_support.revise", f"/api/student-support/{made.json['id']}"))
        body = card["body"]
        self.assertEqual((body["title"], body["state"], body["base_revision"], body["observed"], body["source_key"]), ("Exam 1 total", "todo", 1, [], ""))
        self.assertEqual([commitment["id"] for commitment in body["commitments"]][0], "own")
        self.assertEqual(len(body["commitments"]), 5)
        self.assertEqual(card["summary"], "Track 4 things said in your meeting with Example, Alice on Tue, Nov 10 on their follow-up “Exam 1 total”.")
        # The follow-up changes before Approve: the card's revision is stale, Approve is refused and nothing is written.
        changed = self.client.post(f"/api/student-support/{made.json['id']}", headers=HEADERS, json={
            "request_id": "open-2", "base_revision": 1, "kind": "followup", "enrollment_id": self.alice, "title": "Exam 1 total, again", "state": "todo"})
        self.assertEqual(changed.status_code, 200, changed.json)
        refused = self.approve(card)
        self.assertEqual(refused.json["action"]["status"], "failed", refused.json)
        self.assertEqual(len(self.followups(self.alice)[0]["commitments"]), 1)
        # The instructor's own press proposes it again, on the follow-up as it is now.
        again = self.post(f"{recording}/cards", kind="commitments", request_id="again-1")
        self.assertEqual(again.status_code, 200, again.json)
        self.assertEqual(self.post(f"{recording}/cards", kind="commitments", request_id="again-1").status_code, 200)   # the same press
        card = self.card("student_support")
        self.assertEqual((card["body"]["base_revision"], card["body"]["title"]), (2, "Exam 1 total, again"))
        self.assertEqual(self.post(f"{recording}/cards", kind="commitments").status_code, 409)   # it is waiting
        self.assertEqual(self.approve(card).json["action"]["status"], "succeeded")
        (followup,) = self.followups(self.alice)
        self.assertEqual([commitment["id"] for commitment in followup["commitments"]][0], "own")
        self.assertEqual(len(followup["commitments"]), 5)

    def test_a_private_note_of_the_meeting_is_saved_only_by_approve(self):
        recording = self.visit()
        card = self.card("student_notes")
        self.assertEqual((card["endpoint"], card["path"], card["tier"]), ("student_notes.create", "/api/student-notes", "approve"))
        self.assertEqual((card["body"]["enrollment_id"], card["body"]["date"]), (self.alice, TUE))
        self.assertTrue(card["body"]["text"].startswith("Recorded meeting on Tue, Nov 10.\nYou said you would:\n- " + REGRADE), card["body"]["text"])
        self.assertEqual(card["summary"], "Add a private note about Example, Alice: what was said in your meeting on Tue, Nov 10.")
        self.assertEqual(self.notes(self.alice), [])
        self.assertEqual(self.post(f"{recording}/cards", kind="note").status_code, 409)   # it is waiting
        self.assertEqual(self.approve(card).json["action"]["status"], "succeeded")
        (note,) = self.notes(self.alice)
        self.assertEqual((note["date"], note["text"]), (TUE, card["body"]["text"]))
        self.assertEqual(self.notes(self.bob), [])
        # Once: a saved note is not proposed again, by a later reading or by a press.
        self.assertEqual(self.one(recording)["students"][0]["offer"]["note"], False)
        self.assertEqual(self.post(f"{recording}/cards", kind="note").status_code, 409)
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([found for found in self.mine() if found["endpoint"] == "student_notes.create"], [])
        self.assertEqual(self.post(f"{recording}/cards", kind="grade").status_code, 400)

    def test_a_declined_card_is_proposed_again_only_on_the_instructors_press_and_filing_elsewhere_takes_cards_back(self):
        recording = self.visit()
        card = self.card("student_support")
        self.assertEqual(self.client.post(f"/api/assistant/actions/{card['id']}/decline", headers=HEADERS, json={}).status_code, 200)
        self.assertEqual(self.post(f"{recording}/read").status_code, 200)
        self.record.run_due()
        self.assertEqual([found for found in self.mine() if found["endpoint"].startswith("student_support")], [])
        self.assertTrue(self.one(recording)["students"][0]["offer"]["commitments"])
        self.assertEqual(self.post(f"{recording}/cards", kind="commitments").status_code, 200)
        self.assertEqual(len(self.mine()), 2)
        # Filed as a memo: it is no student's meeting, and both cards that waited are taken back.
        self.assertEqual(self.file("rec-visit", kind="memo").status_code, 200)
        self.assertEqual(self.mine(), [])
        self.assertEqual(self.followups(self.alice), [])

    def test_one_promise_is_never_tracked_twice(self):
        # The worker's proposal and a press in the same instant could both make the card (two processes): the second Approve
        # is refused, since a follow-up never holds what another follow-up of the student already tracks.
        self.visit()
        card = self.card("student_support")
        first = self.client.post("/api/student-support", headers=HEADERS, json={**card["body"], "request_id": str(uuid.uuid4())})
        self.assertEqual(first.status_code, 201, first.json)
        second = self.client.post("/api/student-support", headers=HEADERS, json={**card["body"], "request_id": str(uuid.uuid4())})
        self.assertEqual(second.status_code, 409, second.json)
        self.assertIn("already a commitment on one of this student’s follow-ups", second.json["error"])
        self.assertEqual(self.approve(card).json["action"]["status"], "failed")
        self.assertEqual(len(self.followups(self.alice)), 1)
        # Nor twice in one follow-up, under another identity.
        twice = [*card["body"]["commitments"], {**card["body"]["commitments"][0], "id": "again"}]
        (followup,) = self.followups(self.alice)
        both = self.client.post(f"/api/student-support/{followup['id']}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": followup["revision"], "kind": "followup", "enrollment_id": self.alice,
            "title": followup["title"], "state": "todo", "commitments": twice})
        self.assertEqual(both.status_code, 400, both.json)
        # An ordinary edit of the follow-up, resending its commitments as the page does, is saved as before.
        same = self.client.post(f"/api/student-support/{followup['id']}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": followup["revision"], "kind": "followup", "enrollment_id": self.alice,
            "title": "Meeting on Tue, Nov 10, and after", "state": "todo", "commitments": followup["commitments"]})
        self.assertEqual(same.status_code, 200, same.json)
        self.assertEqual(len(self.followups(self.alice)[0]["commitments"]), 4)

    def test_a_tracked_conversation_gets_them_with_the_messages_it_has_seen_and_the_assistant_keeps_where_each_was_heard(self):
        test_student_support.StudentSupportTests.incoming(self)
        listed = next(entry for entry in self.client.get("/api/student-support", query_string={"kind": "followup"}).json["items"] if entry["source_key"])
        tracked = self.client.post("/api/student-support", headers=HEADERS, json={**listed, "state": "waiting", "request_id": "tracked-1"})
        self.assertEqual(tracked.status_code, 201, tracked.json)
        recording = self.visit()
        card = self.card("student_support")
        self.assertEqual((card["endpoint"], card["body"]["source_key"], card["body"]["observed"], card["body"]["state"]),
                         ("student_support.revise", listed["source_key"], listed["observed"], "waiting"))   # its saved state, untouched
        approved = self.approve(card)
        self.assertEqual((approved.status_code, (approved.json.get("action") or {}).get("status")), (200, "succeeded"), approved.json)
        ids = [entry["id"] for entry in self.one(recording)["items"] if entry["followup"]]
        self.assertEqual(len(ids), 4)
        # The Assistant resolving the follow-up completes its tasks and keeps where each was heard: the recording's page
        # goes on saying they are on her follow-up.
        assistant = self.app.extensions["assistant"]
        result, _ = assistant.run_tool(assistant.open_session("scripted", "Chat"), "track_followup", {
            "enrollment_id": self.alice, "source_key": listed["source_key"], "state": "resolved", "done": "All of it was done on Nov 12."})
        made = json.loads(result)
        self.assertNotIn("error", made, made)
        resolving = assistant._view(*assistant.action(made["action_id"]))
        self.assertEqual(sorted(commitment["recording_item_id"] for commitment in resolving["body"]["commitments"]), sorted(ids))
        self.assertEqual({commitment["state"] for commitment in resolving["body"]["commitments"]}, {"completed"})

    def test_a_student_message_that_still_needs_a_reply_is_never_marked_seen_and_waiting_by_the_card(self):
        test_student_support.StudentSupportTests.incoming(self)
        listed = next(entry for entry in self.client.get("/api/student-support", query_string={"kind": "followup"}).json["items"] if entry["source_key"])
        tracked = self.client.post("/api/student-support", headers=HEADERS, json={**listed, "state": "waiting", "request_id": "tracked-2"})
        self.assertEqual(tracked.status_code, 201, tracked.json)
        test_student_support.StudentSupportTests.incoming(self, ident="m2", at="2026-10-02T12:00:00Z", body="Did you see my note?")
        self.visit()
        body = self.card("student_support")["body"]
        (followup,) = self.followups(self.alice)
        self.assertEqual((followup["saved_state"], followup["state"]), ("waiting", "needs_reply"))
        self.assertEqual((body["state"], body["observed"]), ("needs_reply", followup["observed"]))   # it still needs her reply

    def test_a_commitment_never_rests_on_another_students_meeting(self):
        recording = self.visit()
        regrade = next(found for found in self.one(recording)["items"] if found["sentence"] == REGRADE)
        task = {"id": "t1", "title": "Regrade", "kind": "work", "state": "open", "waiting_on": "you", "recording_item_id": regrade["id"]}
        body = {"kind": "followup", "title": "From a meeting", "state": "todo", "commitments": [task]}
        other = self.client.post("/api/student-support", headers=HEADERS, json={**body, "request_id": "bob-1", "enrollment_id": self.bob})
        self.assertEqual(other.status_code, 400, other.json)
        self.assertIn("this student’s own part", other.json["error"])
        unknown = self.client.post("/api/student-support", headers=HEADERS, json={
            **body, "request_id": "alice-0", "enrollment_id": self.alice, "commitments": [{**task, "recording_item_id": "nothing"}]})
        self.assertEqual(unknown.status_code, 400, unknown.json)
        hers = self.client.post("/api/student-support", headers=HEADERS, json={**body, "request_id": "alice-1", "enrollment_id": self.alice})
        self.assertEqual(hers.status_code, 201, hers.json)
        # A commitment that names no recording is saved as it always was: the key is not added to it.
        plain = self.client.post("/api/student-support", headers=HEADERS, json={
            **body, "request_id": "bob-2", "enrollment_id": self.bob, "commitments": [{key: value for key, value in task.items() if key != "recording_item_id"}]})
        self.assertEqual(plain.status_code, 201, plain.json)
        with self.db() as db:
            saved = json.loads(db.execute("SELECT content_json FROM student_support_revisions WHERE item_id=?", (plain.json["id"],)).fetchone()[0])
        self.assertNotIn("recording_item_id", saved["commitments"][0])


class PartsTests(StudentBase):
    def test_several_students_in_one_recording_each_have_their_part_and_see_no_others(self):
        recording = self.visit()
        self.assertEqual(self.parts(recording, (self.alice, 0, 28000), (self.bob, None, None)).status_code, 400)   # each needs a part
        self.assertEqual(self.parts(recording, (self.alice, 0, 28000), (self.bob, 20000, 36000)).status_code, 400)   # they overlap
        self.assertEqual(self.parts(recording, (self.alice, 0, 28000), (self.alice, 28000, 36000)).status_code, 400)   # one part each
        self.assertEqual(self.parts(recording, (self.alice, 28000, 4000)).status_code, 400)
        self.assertEqual(self.parts(recording, (self.alice, 0, 99999999)).status_code, 400)   # after the recording ends
        set_ = self.parts(recording, (self.alice, 0, 28000), (self.bob, 28000, 36000), request="parts-1")
        self.assertEqual(set_.status_code, 200, set_.json)
        self.assertEqual(self.parts(recording, (self.alice, 0, 1000), (self.bob, 1000, 2000), request="parts-1").status_code, 200)   # the same press
        with self.db() as db:
            links = {link["enrollment_id"]: link for link in self.record._links(db, recording)}
        self.assertEqual({key: (link["part_start_ms"], link["part_end_ms"], link["appointment_id"]) for key, link in links.items()},
                         {self.alice: (0, 28000, self.booked["id"]), self.bob: (28000, 36000, None)})
        self.assertEqual(self.item("rec-visit")["name"], "Example, Alice + Example, Bob · MATH 140 · Fall 2026")
        # Two students: it is read again, told each one's lines; each item is the student's in whose part it was said.
        self.round(later=30)
        facts = self.engine.asked[-1]["payload"]["facts"]["students"]
        self.assertEqual([(student["name"], student["lines"]) for student in facts],
                         [("Example, Alice", {"from": 0, "to": 6}), ("Example, Bob", {"from": 7, "to": 8})])
        page = self.one(recording)
        self.assertEqual({entry["sentence"]: entry["enrollment_id"] for entry in page["items"]},
                         {SICK: self.alice, NOTE: self.alice, REGRADE: self.alice, BACK: self.alice, POSTED: self.bob})
        self.assertEqual([(student["name"], student["part"], student["items"]) for student in page["students"]],
                         [("Example, Alice", {"start_ms": 0, "end_ms": 28000}, 4), ("Example, Bob", {"start_ms": 28000, "end_ms": 36000}, 1)])
        # No student's timeline, card or note is given another student's words.
        (hers,), (his,) = self.timeline(self.alice), self.timeline(self.bob)
        self.assertEqual((hers["context"]["href"], his["context"]["href"]),
                         (f"#recordings/{recording}?t=0&to=28000", f"#recordings/{recording}?t=28000&to=36000"))
        self.assertEqual((POSTED in hers["text"], REGRADE in his["text"], his["text"]), (False, False, f"You said you would:\n- {POSTED}"))
        self.assertEqual(his["at"], "2026-11-10T20:02:28.000Z")   # when his part began
        cards = {(card["endpoint"], card["body"]["enrollment_id"]): card for card in self.mine()}
        self.assertEqual(set(cards), {("student_support.create", self.alice), ("student_notes.create", self.alice),
                                      ("student_support.create", self.bob), ("student_notes.create", self.bob)})
        self.assertEqual([commitment["title"] for commitment in cards[("student_support.create", self.bob)]["body"]["commitments"]], [POSTED])
        self.assertNotIn(POSTED, json.dumps(cards[("student_support.create", self.alice)]["body"]))
        self.assertNotIn(REGRADE, cards[("student_notes.create", self.bob)]["body"]["text"])
        with self.db() as db:
            self.assertEqual(len(meetings.facts(db, self.bob)), 2)
        # With several students a press says whose card.
        for card in self.mine():
            self.client.post(f"/api/assistant/actions/{card['id']}/decline", headers=HEADERS, json={})
        self.assertEqual(self.post(f"{recording}/cards", kind="note").status_code, 400)
        self.assertEqual(self.post(f"{recording}/cards", kind="note", enrollment_id=self.bob).status_code, 200)
        self.assertEqual([card["body"]["enrollment_id"] for card in self.mine()], [self.bob])
        # Moving the line between them: what waited is taken back and made again for the parts as they are now.
        self.assertEqual(self.parts(recording, (self.alice, 0, 16000), (self.bob, 16000, 36000)).status_code, 200)
        self.assertEqual({entry["sentence"]: entry["enrollment_id"] for entry in self.one(recording)["items"]}[REGRADE], self.bob)
        self.assertIn(REGRADE, next(card for card in self.mine() if card["endpoint"] == "student_notes.create")["body"]["text"])
        # One student again, the whole recording theirs.
        self.assertEqual(self.parts(recording, (self.alice, None, None)).status_code, 200)
        self.assertEqual((self.timeline(self.bob), self.timeline(self.alice)[0]["context"]["href"]), ([], f"#recordings/{recording}"))


    def test_filed_again_under_one_of_two_students_they_keep_their_part_and_get_none_of_the_others(self):
        recording = self.visit()
        self.assertEqual(self.parts(recording, (self.alice, 0, 28000), (self.bob, 28000, 36000)).status_code, 200)
        self.round(later=30)
        self.assertEqual({card["body"]["enrollment_id"] for card in self.mine()}, {self.alice, self.bob})
        # "File elsewhere", under Alice: Bob is withdrawn, and his part is nobody's. It never becomes hers.
        self.assertEqual(self.file("rec-visit", kind="student", enrollment_id=self.alice).status_code, 200)
        with self.db() as db:
            (link,) = self.record._links(db, recording)
            self.assertFalse(any(POSTED in line for line in meetings.facts(db, self.alice)))
        self.assertEqual((link["enrollment_id"], link["part_start_ms"], link["part_end_ms"], link["appointment_id"]),
                         (self.alice, 0, 28000, self.booked["id"]))
        page = self.one(recording)   # the reading made when the recording was hers alone is the one shown again
        self.assertEqual(({entry["sentence"]: entry["enrollment_id"] for entry in page["items"]}[POSTED], len(page["students"])), ("", 1))
        self.assertNotIn(POSTED, self.timeline(self.alice)[0]["text"])
        self.assertEqual(self.timeline(self.bob), [])
        # His cards that waited are taken back: he is no longer a student of this recording. Hers still wait, once each.
        self.assertEqual(sorted((card["endpoint"], card["body"]["enrollment_id"]) for card in self.mine()),
                         [("student_notes.create", self.alice), ("student_support.create", self.alice)])
        self.assertNotIn(POSTED, json.dumps([card["body"] for card in self.mine()]))
        # The whole recording is one student's only where the instructor says so.
        self.assertEqual(self.parts(recording, (self.alice, None, None)).status_code, 200)
        self.assertEqual({entry["sentence"]: entry["enrollment_id"] for entry in self.one(recording)["items"]}[POSTED], self.alice)


class AppointmentTests(StudentBase):
    def update(self, appointment, **changes):
        current = next(entry for entry in self.client.get("/api/student-support", query_string={"kind": "appointment"}).json["items"]
                       if entry["id"] == appointment["id"])
        saved = self.client.post(f"/api/student-support/{appointment['id']}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": current["revision"], "kind": "appointment", "enrollment_id": current["enrollment_id"],
            "title": current["title"], "date": current["date"], "time": current["time"], "end_time": current["end_time"],
            "location": current.get("location", ""), "details": current.get("details", ""), "state": current["saved_state"], **changes})
        self.assertEqual(saved.status_code, 200, saved.json)

    def state(self, appointment):
        with self.db() as db:
            rows = db.execute("SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision", (appointment["id"],)).fetchall()
        return [json.loads(row[0])["state"] for row in rows]

    def test_inside_an_appointment_recorded_as_a_no_show_it_is_marked_and_faculty_changes_neither(self):
        recording = self.visit()
        self.assertEqual((self.one(recording)["students"][0]["off"], self.listing()["attention"]["appointments_off"]), ("", 0))
        self.update(self.booked, state="no_show")
        page = self.one(recording)
        self.assertEqual(page["students"][0]["off"], "Their 2:00 PM appointment that day is recorded as a no-show, yet this recording began in it. "
                         "Correct the appointment if they came, or file this recording elsewhere.")
        row = self.item("rec-visit")
        self.assertEqual((row["links"][0]["appointment_off"], row["filing"]), ("no_show", "filed"))
        attention = self.listing()["attention"]
        self.assertEqual((attention["appointments_off"], self.record.attention()["appointments_off"]), (1, 1))
        self.assertEqual(self.state(self.booked), ["scheduled", "no_show"])   # nothing of the appointment is changed
        self.update(self.booked, state="attended")
        self.assertEqual((self.one(recording)["students"][0]["off"], self.listing()["attention"]["appointments_off"]), ("", 0))

    def test_a_cancelled_appointment_files_only_where_nothing_else_was_on_the_calendar(self):
        booked = self.appointment(ALICE, TUE, "14:00", "14:30", state="cancelled")
        late = self.appointment(BOB, TUE, "16:00", "16:30", state="cancelled")
        self.google.add("MATH 140 - all instructors", TUE, "16:00", "17:00")
        self.fake.add("came-after-all", campus(TUE, "14:05"), minutes=12, transcript=VISIT)
        self.fake.add("the-meeting", campus(TUE, "16:05"), minutes=40, transcript=VISIT)
        self.round()
        row = self.item("came-after-all")
        self.assertEqual((row["filing"], row["name"], row["links"][0]["appointment_off"]), ("filed", "Example, Alice · MATH 140 · Fall 2026", "cancelled"))
        with self.db() as db:
            (link,) = self.record._links(db, row["id"])
        self.assertEqual((link["appointment_id"], json.loads(link["basis_json"])["cancelled"], link["filed_by"]), (booked["id"], True, "calendar"))
        self.assertIn("is recorded as cancelled, yet this recording began in it.", self.one(row["id"])["students"][0]["off"])
        self.assertEqual(self.state(booked), ["cancelled"])
        # Another entry at that time decides as if the cancelled appointment were not there.
        self.assertEqual((self.item("the-meeting")["name"], self.state(late)), ("MATH 140 · Fall 2026", ["cancelled"]))
        # While Google's calendar cannot vouch that nothing else was there, it waits.
        self.appointment(BOB, "2026-11-09", "09:00", "09:30", state="cancelled")
        self.google.gap = "Faculty has not read your Google Calendar since yesterday"
        self.fake.add("unsure", campus("2026-11-09", "09:05"), minutes=5, transcript=VISIT)
        self.round(later=700)
        waiting = self.item("unsure")
        self.assertEqual((waiting["filing"], waiting["filing_detail"]),
                         ("needs_filing", f"{filing.CANCELLED} Faculty has not read your Google Calendar since yesterday."))


if __name__ == "__main__":
    unittest.main()
