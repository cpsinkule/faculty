"""A COORDINATOR'S FOLLOW-UP (ASSISTANT, TEACHING-WORKFLOWS, COORDINATOR): the coordinator's conversation is prepared like a
student's, under its own instructions; a reply reaches only the coordinator, the term's TAs and the instructor; nothing is done by
itself; what is a student's alone stays out.

Synthetic catalogs and scripted engines only. No configured model, mailbox or outside service is reached."""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from communications.record import record
from course_coordinators import service as coordinators
from rosters import connect
import test_followup_suggestions as suggestions
from test_followup_suggestions import ScriptedEngine, proposing
from test_storage import HEADERS

LEE = {"coordinated": True, "name": "Dr. Jordan Lee", "email": "jlee@tamu.edu"}
ASKED = "Please send me the names of your two proctors for the final by Monday. The room list is attached."


class CoordinatorTests(suggestions.SuggestionsTests):
    """On the suggestion tests' own fixture (its helpers: prepare, session, tool_write, fake_mail); `load_tests` below runs only
    the tests written here, never that class's again."""

    def setUp(self):
        super().setUp()
        course = self.client.get("/api/state").json["courses"][0]
        saved = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "coordinator": LEE}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.owner = coordinators.owner_key(self.term)

    def later(self, seconds=60):
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")

    def wrote(self, ident="c1", body=ASKED, thread="c9", seconds=60, mine=False, subject="Final exam proctors"):
        """One captured message of the coordinator's conversation: theirs (`other`, linked as its author), or the instructor's."""
        with self.rosters_lock(), connect(self.assistant.database) as db:
            record(db, "gmail", ident, {"sent_at": self.later(seconds), "author_role": "instructor" if mine else "other",
                                        "author": {"name": "Prof" if mine else "Jordan Lee", "email": "prof@tamu.edu" if mine else "jlee@tamu.edu"},
                                        "recipients": [], "subject": subject, "body_text": body, "attachments": [], "context": {"thread_id": thread}},
                   [(self.owner, self.term, "to" if mine else "author", "coordinator_address")], thread)
            db.commit()

    def item(self):
        return next(i for i in self.listing()["items"] if i["enrollment_id"] == self.owner)

    def coordinator_session(self, seconds=60):
        self.wrote(seconds=seconds)
        self.suggestions.process_one()
        run = self.item()["suggestions"]
        with connect(self.assistant.database) as db:
            self.suggestions.context.remember(db, self.suggestions.get(db, run["id"]))
        session = self.assistant.open_session("test")
        session.update(_proposal_only=True, _followup_run=run["id"])
        return session

    def test_it_is_prepared_under_the_coordinators_instructions_and_every_step_is_a_card(self):
        self.fake_mail()
        self.wrote()
        reply = ("/api/mail/send", {"to": ["jlee@tamu.edu"], "subject": "Re: Final exam proctors", "body": "Sam Park and Jo Kim will proctor."},
                 "Reply to the coordinator")
        track = ("/api/student-support", lambda c: {"enrollment_id": c.run["enrollment_id"], "kind": "followup", "source_key": c.run["source_key"],
                                                    "title": "Final exam proctors", "state": "resolved"}, "Mark it resolved")
        respond = proposing(reply, track)
        self.suggestions.automatic = True   # DONE FOR YOU is on: still nothing here is done by itself
        self.assertTrue(self.suggestions.process_one())
        engine = ScriptedEngine(respond)
        with patch("assistant.conversations.engine_for", return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        run = self.item()["suggestions"]
        self.assertEqual(run["state"], "ready", run)
        # Told who this is, and none of a student's rules.
        self.assertIn("COURSE COORDINATOR", engine.prompt)
        self.assertIn("NOT a student", engine.prompt)
        self.assertIn("Dr. Jordan Lee <jlee@tamu.edu>, the coordinator of MATH 140 · Fall 2026", engine.prompt)
        self.assertIn("sections 507, 508", engine.prompt)
        self.assertIn("/api/communications/coordinators/" + self.term, engine.prompt)
        for student_rule in ("ONE student follow-up", "office-hours appointment (POST", "Student Rule 7", "/api/communications/students/"):
            self.assertNotIn(student_rule, engine.prompt)
        self.assertNotIn(ASKED, engine.prompt)   # the conversation is read through Faculty, never copied into the instruction
        # Both steps wait as cards, the status too, and they name the coordinator.
        self.assertEqual([o["outcome"] for o in respond.outcomes], ["waiting_for_approval", "waiting_for_approval"], respond.outcomes)
        cards = run["actions"]
        self.assertEqual([c["tier"] for c in cards], ["approve", "approve"])
        self.assertEqual([c["effect"]["title"] for c in cards], ["Send an email", "Mark this conversation resolved"])
        self.assertEqual(cards[1]["effect"]["audience"], "Saved in Faculty only. The coordinator is not told.")
        self.assertEqual(run.get("done", []), [])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT enrollment_id FROM student_support_suggestion_runs").fetchone()[0], self.owner)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gmail_mail_sends").fetchone()[0], 0)
            self.assertNotIn(ASKED, db.execute("SELECT source_json FROM student_support_suggestion_runs").fetchone()[0])
        # The status waits for the reply before it, as a student's does.
        self.assertEqual(self.approve(cards[1]).status_code, 409)

    def test_an_approved_status_is_saved_under_the_terms_coordinator(self):
        self.wrote(body="Grades are due Friday at noon.", subject="Grades due")
        todo = ("/api/student-support", lambda c: {"enrollment_id": c.run["enrollment_id"], "kind": "followup", "source_key": c.run["source_key"],
                                                   "title": "Grades due", "state": "todo", "next": "Certify the final and sync grades by Friday noon"},
                "Mark it To do")
        self.assertTrue(self.suggestions.process_one())
        with patch("assistant.conversations.engine_for", return_value=ScriptedEngine(proposing(todo))):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        card = self.item()["suggestions"]["actions"][0]
        self.assertEqual((card["tier"], card["effect"]["title"]), ("approve", "Mark this conversation To do"))
        approved = self.approve(card)
        self.assertEqual(approved.status_code, 200, approved.json)
        with connect(self.assistant.database) as db:
            self.assertEqual(tuple(db.execute("SELECT term_id,enrollment_id,kind FROM student_support_items").fetchone()),
                             (self.term, self.owner, "followup"))
        item = self.item()
        self.assertEqual((item["state"], item["next"], item["student"]["name"]),
                         ("todo", "Certify the final and sync grades by Friday noon", "Dr. Jordan Lee"))

    def test_it_is_told_how_to_handle_what_a_coordinator_usually_sends(self):
        # Read from a term of the coordinator's real mail (2026-11-15): agendas as Word files, grading rulings, deadlines,
        # counts, links behind a sign-in, files for the course and answers about a particular student.
        self.wrote()
        self.assertTrue(self.suggestions.process_one())
        engine = ScriptedEngine(proposing())
        with patch("assistant.conversations.engine_for", return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        for told in ("a Word or Excel file comes\n  back as its words", "class-schedule", "bubble-sheet key change only where Faculty\n  differs",
                     "never propose a\n  regrade or recalculation of a certified assessment unless the instructor asked",
                     "Faculty cannot open it", "you cannot file them as Course notes", "find_student",
                     "The\n  reply to the student is prepared from the student's own follow-up, never here"):
            self.assertIn(told, engine.prompt)

    def test_an_answer_about_a_student_is_proposed_on_that_students_own_follow_up(self):
        # Their answer to what the instructor asked about a student: a card on the student's follow-up, never mail to the student.
        self.fake_mail()
        self.incoming()   # Alice's own conversation, tracked as waiting on the coordinator's answer
        listed = next(i for i in self.listing(False)["items"] if i["enrollment_id"] == self.alice)
        tracked = self.client.post("/api/student-support", json={"kind": "followup", "enrollment_id": self.alice, "source_key": listed["source_key"],
                                                                 "title": "Makeup exam length", "state": "todo", "next": "Ask the coordinator how long",
                                                                 "observed": listed["observed"], "request_id": "r-" + "7" * 30}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        session = self.coordinator_session()
        answer = self.tool_write(session, "/api/student-support/" + tracked.json["id"], {
            "kind": "followup", "title": "Makeup exam length", "state": "todo", "base_revision": 1, "observed": listed["observed"],
            "next": "Coordinator: the makeup is 75 minutes. Tell Alice the time and length"})
        self.assertEqual(answer["outcome"], "waiting_for_approval", answer)
        self.assertIn("a change the coordinator’s message calls for", answer["why_approval"])
        card = next(a for a in self.item()["suggestions"]["actions"] if a["id"] == answer["action_id"])
        self.assertEqual((card["tier"], card["effect"]["own"]), ("approve", False))
        self.assertIn("never a student", self.tool_write(session, "/api/mail/send", {"to": ["alice@example.test"], "subject": "Makeup",
                                                                                     "body": "It is 75 minutes."})["error"])
        approved = self.approve(card)
        self.assertEqual(approved.status_code, 200, approved.json)
        after = next(i for i in self.listing(False)["items"] if i["enrollment_id"] == self.alice)
        self.assertEqual(after["next"], "Coordinator: the makeup is 75 minutes. Tell Alice the time and length")

    def test_a_students_follow_up_is_prepared_as_before(self):
        self.incoming()
        run, engine = self.prepare()
        self.assertEqual(run["state"], "ready")
        self.assertIn("ONE student follow-up", engine.prompt)
        self.assertNotIn("COURSE COORDINATOR", engine.prompt)

    def test_mail_reaches_the_coordinator_the_terms_tas_and_you_only(self):
        self.fake_mail()
        session = self.coordinator_session()
        with connect(self.assistant.database) as db:
            section = db.execute("SELECT id FROM course_sections WHERE number=508").fetchone()[0]
        saved = self.client.post(f"/api/teaching-assistants/sections/{section}", json={"request_id": "tas", "base_revision": 0,
            "contacts": [{"name": "Tess Assistant", "email": "tess@example.test"}]}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        note = {"subject": "Re: Final exam proctors", "body": "Sam Park and Jo Kim will proctor."}
        for allowed in ({"to": ["jlee@tamu.edu"]}, {"to": ["JLee@email.tamu.edu"], "cc": ["tess@example.test", "prof@tamu.edu"]},
                        {"to": ["tess@example.test"]}):
            answer = self.tool_write(session, "/api/mail/send", {**note, **allowed})
            self.assertEqual(answer["outcome"], "waiting_for_approval", answer)
        for refused in ({"to": ["alice@example.test"]}, {"to": ["jlee@tamu.edu"], "cc": ["bob@example.test"]},
                        {"to": ["jlee@tamu.edu"], "bcc": ["stranger@example.test"]}):
            self.assertIn("never a student", self.tool_write(session, "/api/mail/send", {**note, **refused})["error"])

    def test_a_meeting_the_coordinator_calls_is_a_calendar_event_and_an_appointment_is_refused(self):
        session = self.coordinator_session()
        event = self.tool_write(session, "/api/calendar/events", {"title": "MATH 140 instructors meeting", "date": "2026-12-01",
                                                                  "time": "15:00", "end_time": "16:00", "category": "event"})
        self.assertEqual(event.get("outcome"), "waiting_for_approval", event)
        appointment = self.tool_write(session, "/api/student-support", {"enrollment_id": self.owner, "kind": "appointment", "title": "Meet",
                                                                        "date": "2026-12-01", "time": "15:00", "end_time": "15:30"})
        self.assertIn("meeting with the coordinator is a calendar event", json.dumps(appointment))

    def test_waiting_is_marked_only_beside_a_reply_and_nothing_closes_by_itself(self):
        self.fake_mail()
        session = self.coordinator_session(seconds=1)
        time.sleep(1.2)   # a reply answers their message only when it is written after it
        waiting = {"enrollment_id": self.owner, "kind": "followup", "source_key": self.item()["source_key"], "title": "Final exam proctors",
                   "state": "waiting", "next": "Their answer about the room"}
        self.assertIn("propose the reply first", self.tool_write(session, "/api/student-support", waiting)["error"])
        self.tool_write(session, "/api/mail/send", {"to": ["jlee@tamu.edu"], "subject": "Re: Final exam proctors", "body": "Which room?"})
        after = self.tool_write(session, "/api/student-support", waiting)
        self.assertEqual(after.get("outcome"), "waiting_for_approval", after)
        for close in (self.suggestions.close_acknowledged, self.suggestions.close_on_evidence, self.suggestions.close_decided_todo):
            self.assertFalse(close())

    def test_the_context_read_names_the_coordinator_and_where_a_reply_goes(self):
        self.wrote()
        self.wrote(ident="c2", body="We will confirm.", seconds=30, mine=True)   # earlier than theirs: they wrote last
        answer = self.client.get(f"/api/student-support/students/{self.owner}/context")
        self.assertEqual(answer.status_code, 200, answer.json)
        read = answer.json
        self.assertEqual((read["coordinator"]["name"], read["coordinator"]["coordinator"], read["term_id"]), ("Dr. Jordan Lee", True, self.term))
        self.assertEqual(read["reply"], {"addresses": ["jlee@tamu.edu"], "thread_id": "c9"})
        self.assertIn("COURSE COORDINATOR, not a student", read["facts"])
        for students_alone in ("student", "notes", "grades"):
            self.assertNotIn(students_alone, read)
        self.assertEqual(self.client.get(f"/api/student-support/students/{coordinators.owner_key('none')}/context").status_code, 404)

    def test_a_course_no_longer_coordinated_is_not_prepared(self):
        self.wrote()
        course = self.client.get("/api/state").json["courses"][0]
        self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"],
                                                                "coordinator": {"coordinated": False, "name": "", "email": ""}}, headers=HEADERS)
        self.assertFalse(self.suggestions.process_one())
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_suggestion_runs").fetchone()[0], 0)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(CoordinatorTests(name) for name in loader.getTestCaseNames(CoordinatorTests) if name in vars(CoordinatorTests))


if __name__ == "__main__":
    unittest.main()
