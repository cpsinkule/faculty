"""THE COORDINATOR'S MAIL (COMMUNICATIONS, COORDINATOR) offline: mail with a coordinated term's coordinator is kept like a
student's and linked to the TERM under its owner key; it is no student's message; a student's timeline never shows it; nothing
in Gmail changes."""
import json
import time
import unittest

from test_communications import ACCOUNT, ALICE, BOB, Base, mail, millis
from test_storage import HEADERS

from course_coordinators import service as coordinators

LEE = {"coordinated": True, "name": "Dr. Jordan Lee", "email": "jlee@tamu.edu"}


class CoordinatorBase(Base):
    def coordinate(self, answer=LEE):
        course = self.client.get("/api/state").json["courses"][0]
        saved = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "coordinator": answer}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.owner = coordinators.owner_key(self.term)

    def coordinator_timeline(self):
        answer = self.client.get(f"/api/communications/coordinators/{self.term}")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def add_mail(self):
        self.gmail.add("e1", mail("Jordan Lee <jlee@tamu.edu>", "course-instructors@lists.tamu.edu", "Exam 3 rooms",
                                  "Rooms are attached. Please confirm your proctors by Friday.",
                                  files=[("rooms.pdf", b"%PDF-1.4 rooms", "application/pdf")]), at=millis(20), thread="e1" + "0" * 6)
        self.gmail.add("e2", mail(f"Prof <{ACCOUNT}>", "jlee@tamu.edu", "Re: Exam 3 rooms", "Two proctors confirmed."),
                       at=millis(21), labels=("SENT",), thread="e1" + "0" * 6)
        self.gmail.add("e3", mail("Alice Example <alice@tamu.edu>", ACCOUNT, "Exam conflict", "I have another exam then.", cc="jlee@email.tamu.edu"),
                       at=millis(22), thread="e3" + "0" * 6)
        self.gmail.add("x1", mail("Registrar <reg@tamu.edu>", ACCOUNT, "Grades due", "Not the coordinator."), at=millis(23))


class CaptureTests(CoordinatorBase):
    def test_nothing_of_the_coordinators_is_kept_until_the_course_is_coordinated(self):
        self.add_mail()
        outcome = self.capture("gmail")
        self.assertEqual(outcome["status"], "ok", dict(outcome))
        counts = json.loads(outcome["counts_json"])
        self.assertEqual(counts["new"], 1, counts)   # only the student's own message
        self.assertEqual(self.coordinator_timeline()["entries"], [])
        self.assertEqual(self.coordinator_timeline()["coordinator"]["coordinated"], False)

    def test_the_coordinators_mail_is_kept_and_linked_to_the_term(self):
        self.coordinate()
        self.add_mail()
        outcome = self.capture("gmail")
        self.assertEqual(outcome["status"], "ok", outcome)
        self.assertEqual(json.loads(outcome["counts_json"])["new"], 3)
        self.assertTrue(all(call[0] == "GET" for call in self.gmail.calls))
        read = self.coordinator_timeline()
        self.assertEqual(read["coordinator"]["name"], "Dr. Jordan Lee")
        self.assertEqual([(e["subject"], e["author_role"], e["roles"]) for e in read["entries"]],
                         [("Exam conflict", "student", ["cc"]), ("Re: Exam 3 rooms", "instructor", ["to"]), ("Exam 3 rooms", "other", ["author"])])
        self.assertEqual(read["entries"][2]["attachments"][0]["name"], "rooms.pdf")
        with self.db() as db:
            links = {(r["enrollment_id"], r["role"], r["matched_by"]) for r in db.execute("SELECT enrollment_id,role,matched_by FROM comm_links")}
            self.assertEqual(links, {(self.owner, "author", "coordinator_address"), (self.owner, "to", "coordinator_address"),
                                     (self.owner, "cc", "coordinator_address"), (self.students[ALICE], "author", "roster_address")})
            self.assertFalse(db.execute("SELECT 1 FROM comm_messages WHERE external_id='x1'").fetchone())
        # A student's timeline shows only their own message, and the coordinator's key is nobody's student timeline.
        self.assertEqual([e["subject"] for e in self.timeline(ALICE)], ["Exam conflict"])
        self.assertEqual(self.timeline(BOB), [])
        self.assertEqual(self.client.get(f"/api/communications/students/{self.owner}").status_code, 404)
        # A second round keeps nothing twice.
        self.assertEqual(json.loads(self.capture("gmail")["counts_json"])["new"], 0)

    def test_mail_kept_before_the_course_was_coordinated_is_linked_without_a_read(self):
        self.add_mail()
        self.capture("gmail")
        self.coordinate()
        reads = sum(1 for call in self.gmail.calls if call[1].startswith("/messages/"))
        self.capture("gmail")
        # The student's message that copied the coordinator was already kept: it gains the coordinator's link from its kept addresses.
        self.assertIn("Exam conflict", [e["subject"] for e in self.coordinator_timeline()["entries"]])
        self.assertEqual(sum(1 for call in self.gmail.calls if call[1].startswith("/messages/")), reads + 2)   # e1 and e2 only

    def test_a_link_must_name_an_enrollment_of_its_term_or_the_terms_own_coordinator_key(self):
        self.coordinate()
        self.add_mail()
        self.capture("gmail")
        with self.db() as db:
            message = db.execute("SELECT id FROM comm_messages LIMIT 1").fetchone()[0]
            for values in ((message, "9999999999", self.term, "to", "roster_address"),
                           (message, coordinators.owner_key("another-term"), self.term, "to", "coordinator_address"),
                           (message, self.owner, self.term, "bcc", "roster_address"),
                           (message, self.students[BOB], self.term, "bcc", "coordinator_address")):
                with self.subTest(values=values[1:]), self.assertRaises(Exception):
                    db.execute("INSERT INTO comm_links(id,message_id,enrollment_id,term_id,role,matched_by,created_at) VALUES('n',?,?,?,?,?,'2026-11-14')", values)
            for statement in ("UPDATE comm_links SET role='cc'", "DELETE FROM comm_links"):
                with self.subTest(statement=statement), self.assertRaises(Exception):
                    db.execute(statement)


class FollowupTests(CoordinatorBase):
    """A COORDINATOR'S FOLLOW-UP (TEACHING-WORKFLOWS): the coordinator's conversation is listed and tracked as a student's is,
    owned by the term."""

    def setUp(self):
        super().setUp()
        self.coordinate()
        self.add_mail()
        self.now = int(time.time() * 1000)

    def followups(self, **more):
        query = "&".join(["kind=followup", *[f"{key}={value}" for key, value in more.items()]])
        answer = self.client.get(f"/api/student-support?{query}")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json["items"]

    def new_mail(self):
        self.gmail.add("e7", mail("Jordan Lee <jlee@tamu.edu>", "course-instructors@lists.tamu.edu", "Final exam proctors",
                                  "Please send me your proctors' names by Monday."), at=self.now + 60000, thread="e7" + "0" * 6)

    def test_mail_from_before_the_card_named_the_coordinator_is_background_and_what_comes_after_is_a_follow_up(self):
        self.capture("gmail")
        # September's mail was kept, but the course card named the coordinator today: only the student's own message waits.
        self.assertEqual([(i["title"], i["student"]["name"]) for i in self.followups()], [("Exam conflict", "Example, Alice")])
        self.new_mail()
        self.capture("gmail")
        listed = {i["title"]: i for i in self.followups()}
        item = listed["Final exam proctors"]
        self.assertEqual((item["enrollment_id"], item["term_id"], item["state"], item["attention"], item["id"]),
                         (self.owner, self.term, "needs_reply", True, ""))
        self.assertEqual(item["student"], {"enrollment_id": self.owner, "coordinator": True, "name": "Dr. Jordan Lee", "email": "jlee@tamu.edu",
                                           "term_id": self.term, "code": "MATH 140", "term_label": "Fall 2026", "sections": []})
        self.assertNotIn("coordinator", listed["Exam conflict"]["student"])
        self.assertEqual(self.app.extensions["student_support"].attention()["followup"], 2)
        # Read for the coordinator alone, as a student's follow-ups are read for one enrollment.
        self.assertEqual([i["title"] for i in self.followups(enrollment_id=self.owner)], ["Final exam proctors"])

    def test_the_instructors_reply_answers_it_and_a_student_copying_the_coordinator_is_not_their_conversation(self):
        self.new_mail()
        self.gmail.add("e8", mail(f"Prof <{ACCOUNT}>", "jlee@tamu.edu", "Re: Final exam proctors", "Sam Park and Jo Kim."),
                       at=self.now + 90000, labels=("SENT",), thread="e7" + "0" * 6)
        self.capture("gmail")
        self.assertEqual([i["title"] for i in self.followups()], ["Exam conflict"])
        self.assertEqual(self.followups(enrollment_id=self.owner), [])

    def test_it_is_tracked_under_the_term_and_reopens_when_the_coordinator_writes_again(self):
        self.new_mail()
        self.capture("gmail")
        item = self.followups(enrollment_id=self.owner)[0]
        body = {"kind": "followup", "enrollment_id": self.owner, "source_key": item["source_key"], "title": "Send proctors' names",
                "state": "todo", "next": "Ask the TAs who can proctor", "observed": item["observed"], "request_id": "r-" + "1" * 30}
        saved = self.client.post("/api/student-support", json=body, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        with self.db() as db:
            row = db.execute("SELECT term_id,enrollment_id,kind,source_key FROM student_support_items").fetchone()
            self.assertEqual(tuple(row), (self.term, self.owner, "followup", item["source_key"]))
        tracked = self.followups(enrollment_id=self.owner)[0]
        self.assertEqual((tracked["state"], tracked["next"], tracked["student"]["coordinator"]), ("todo", "Ask the TAs who can proctor", True))
        revised = self.client.post(f"/api/student-support/{tracked['id']}", json={**body, "state": "resolved", "base_revision": 1,
                                                                                 "request_id": "r-" + "2" * 30}, headers=HEADERS)
        self.assertEqual(revised.status_code, 200, revised.json)
        self.assertEqual(self.followups(enrollment_id=self.owner)[0]["attention"], False)
        self.gmail.add("e9", mail("Jordan Lee <jlee@tamu.edu>", ACCOUNT, "Re: Final exam proctors", "One more thing: which rooms?"),
                       at=self.now + 120000, thread="e7" + "0" * 6)
        self.capture("gmail")
        again = self.followups(enrollment_id=self.owner)[0]
        self.assertEqual((again["state"], again["new_reply"], again["attention"]), ("needs_reply", True, True))

    def test_what_is_a_students_alone_is_refused_for_the_coordinator(self):
        self.new_mail()
        self.capture("gmail")
        item = self.followups(enrollment_id=self.owner)[0]
        appointment = self.client.post("/api/student-support", json={
            "kind": "appointment", "enrollment_id": self.owner, "title": "Meet", "date": "2026-12-01", "time": "10:00", "end_time": "10:30",
            "location": "BLOC 309", "request_id": "r-" + "3" * 30}, headers=HEADERS)
        self.assertEqual(appointment.status_code, 400)
        self.assertIn("meeting with a student", appointment.json["error"])
        join = self.client.post("/api/student-support/followups/join", json={
            "enrollment_id": self.owner, "source_key": item["source_key"], "with_key": "gmail:other", "request_id": "r-" + "4" * 30}, headers=HEADERS)
        self.assertEqual(join.status_code, 404)
        other = self.client.post("/api/student-support", json={"kind": "followup", "enrollment_id": coordinators.owner_key("no-such-term"),
                                                               "title": "x", "request_id": "r-" + "5" * 30}, headers=HEADERS)
        self.assertEqual(other.status_code, 404)

    def test_the_assistant_reads_the_word_file_the_coordinator_attached(self):
        # ASSISTANT "FILES" › WORD AND EXCEL FILES (2026-11-15): the coordinator's agendas are Word files, kept with the message.
        from test_assistant_files import DOCX, office_file
        word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        self.gmail.add("eb", mail("Jordan Lee <jlee@tamu.edu>", ACCOUNT, "Wednesday's agenda", "Attached is the agenda for Wednesday.",
                                  files=[("Week_13_Wednesday_Meeting.docx", office_file({"word/document.xml": DOCX}), word)]),
                       at=self.now + 60000, thread="eb" + "0" * 6)
        self.capture("gmail")
        entry = next(e for e in self.coordinator_timeline()["entries"] if e["subject"] == "Wednesday's agenda")
        self.assertEqual(entry["attachments"][0]["name"], "Week_13_Wednesday_Meeting.docx")
        assistant = self.app.extensions["assistant"]
        text, _ = assistant.run_tool(assistant.open_session("test"), "fetch_files", {
            "paths": [f"/api/communications/messages/{entry['message_id']}/attachments/0"], "look": True, "show": False})
        read = json.loads(text)["files"][0]
        self.assertIn("its words below", read["looked"])
        self.assertIn("1. Exam 3 rooms\tHELD 111", read["text"])

    def test_a_course_no_longer_coordinated_lists_no_new_suggestion_and_keeps_what_was_tracked(self):
        self.new_mail()
        self.capture("gmail")
        item = self.followups(enrollment_id=self.owner)[0]
        self.client.post("/api/student-support", json={"kind": "followup", "enrollment_id": self.owner, "source_key": item["source_key"],
                                                       "title": "Proctors", "state": "todo", "observed": item["observed"],
                                                       "request_id": "r-" + "6" * 30}, headers=HEADERS)
        self.gmail.add("ea", mail("Jordan Lee <jlee@tamu.edu>", ACCOUNT, "Grades due", "Grades are due Friday."), at=self.now + 150000, thread="ea" + "0" * 6)
        self.capture("gmail")
        self.assertEqual(len(self.followups(enrollment_id=self.owner)), 2)
        self.coordinate({"coordinated": False, "name": "", "email": ""})
        kept = self.followups(enrollment_id=self.owner)
        self.assertEqual([(i["title"], i["student"]["name"], i["student"]["email"]) for i in kept], [("Proctors", "Dr. Jordan Lee", "")])


if __name__ == "__main__":
    unittest.main()
