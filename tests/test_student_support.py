"""TEACHING-WORKFLOWS: identities, immutable decisions, stale writes and the calendar projection that names the student."""
import json
import sqlite3
import uuid
from datetime import datetime
from unittest.mock import patch

from communications.record import record
from rosters import connect
from test_student_notes import Base
from test_storage import HEADERS


class StudentSupportTests(Base):
    RESET = "/api/student-support/followups/start-now"

    def reset_preview(self, through="2026-10-01T12:00:00Z"):
        response = self.client.post(self.RESET + "/preview", json={"through": through}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def save_support(self, **changes):
        data = {"kind": "appointment", "enrollment_id": self.alice, "request_id": "meeting-1",
                "title": "Discuss a private concern", "date": "2026-10-01", "time": "14:00", "end_time": "14:30", "location": "Office"}
        data.update(changes)
        return self.client.post("/api/student-support", json=data, headers=HEADERS)

    def incoming(self, ident="m1", at="2026-10-01T12:00:00Z", thread="one", author="student", enrollment=None, body="Help, please"):
        with connect(self.app.config["DATABASE"]) as db:
            record(db, "gmail", ident, {"sent_at": at, "author_role": author,
                   "author": {"name": "Alice" if author == "student" else "Instructor"}, "recipients": [], "subject": "Question",
                   "body_text": body, "attachments": [], "context": {"thread_id": thread}},
                   [(enrollment or self.alice, self.term, "author" if author == "student" else "to", "roster_address")], thread)
            db.commit()

    def listing(self, kind="followup"):
        response = self.client.get("/api/student-support", query_string={"kind": kind})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["items"]

    def test_appointment_read_matches_full_projection_without_reading_conversations(self):
        # PERFORMANCE / TEACHING-WORKFLOWS: identical bytes, visibility, revisions and order, even with unrelated follow-ups.
        service = self.app.extensions["student_support"]
        for index, (state, day) in enumerate((("scheduled", "2026-10-01"), ("attended", "2026-10-01"),
                                             ("scheduled", "2026-10-12"), ("cancelled", "2026-10-01"),
                                             ("no_show", "2026-10-02"), ("scheduled", "2026-10-01"))):
            result = self.save_support(request_id=f"meeting-{index}", state=state, date=day,
                                       enrollment_id=self.bob if index == 5 else self.alice)
            self.assertEqual(result.status_code, 201, result.json)
        self.incoming()
        followup = self.listing()[0]
        self.assertEqual(self.client.post("/api/student-support", json={**followup, "state": "waiting", "request_id": "wait"},
                                          headers=HEADERS).status_code, 201)

        def full_projection(db, enrollment_id, since):
            return [item for item in service._items(db, enrollment_id, since) if item["kind"] == "appointment"]

        def compare(enrollment=None):
            query = {"kind": "appointment", **({"enrollment_id": enrollment} if enrollment else {})}
            with patch.object(service, "_appointments", side_effect=full_projection):
                expected = self.client.get("/api/student-support", query_string=query)
            with patch("student_support.service.conversations", side_effect=AssertionError("PERFORMANCE: unrelated messages read")), \
                    patch.object(service.triage, "apply", side_effect=AssertionError("PERFORMANCE: unrelated triage read")):
                actual = self.client.get("/api/student-support", query_string=query)
            self.assertEqual((actual.status_code, actual.data), (expected.status_code, expected.data))
            return actual.json

        with patch("student_support.service.now", return_value=datetime(2026, 10, 8, 12)):
            current = compare()
            self.assertEqual(len(current["items"]), 6)
            # MEETINGS COMPLETE THEMSELVES (2026-10-21): the two past scheduled meetings read as Attended and need nothing.
            self.assertEqual([i["attention"] for i in current["items"]], [False] * 6)
            self.assertEqual(sorted((i["date"], i["state"], i["saved_state"]) for i in current["items"] if i["saved_state"] == "scheduled"),
                             [("2026-10-01", "attended", "scheduled"), ("2026-10-01", "attended", "scheduled"), ("2026-10-12", "scheduled", "scheduled")])
            self.assertEqual(len(compare(self.alice)["items"]), 5)
            self.assertEqual(len(compare(self.bob)["items"]), 1)
            with connect(service.path) as db:
                db.execute("UPDATE course_terms SET is_current=0")
                db.commit()
            self.assertEqual(compare()["items"], [])
            self.assertEqual(len(compare(self.alice)["items"]), 5)

    def test_calendar_names_the_student_and_outcome_changes_no_note_or_grade(self):
        # CALENDAR OFFICE HOURS (requirement, 2026-10-06): the Calendar page, Home and Google show whose it is and what for; private details and
        # the enrollment ID stay off it.
        response = self.save_support(details="Keep this private")
        self.assertEqual(response.status_code, 201, response.json)
        same = self.save_support(details="Keep this private")
        self.assertEqual(same.json, response.json)
        from student_support.service import appointment_title
        self.assertEqual([appointment_title(p, "Walsh, Kenna B") for p in ("Office Hours", " office-hours. ", "Quiz 4  questions", "")],
                         ["Office hours: Walsh, Kenna B", "Office hours: Walsh, Kenna B", "Office hours: Walsh, Kenna B · Quiz 4 questions",
                          "Office hours: Walsh, Kenna B"])  # a purpose that only says office hours is not repeated
        self.assertEqual(appointment_title("Quiz 4 questions"), "Office hours · Quiz 4 questions")  # no current roster name
        self.assertEqual(self.save_support(title="Different", details="Keep this private").status_code, 409)
        name = self.app.extensions["student_support"].people()[self.alice]["name"]
        feed = self.client.get("/api/calendar?start=2026-10-01&end=2026-10-02").json
        entry = next(e for e in feed["events"] if e["source"] == "office_hours")
        self.assertEqual((entry["title"], entry["time"], entry["end_time"]), (f"Office hours: {name} · Discuss a private concern", "14:00", "14:30"))
        for secret in (self.alice, "Keep this private"):
            self.assertNotIn(secret, json.dumps(entry))
        from datetime import date
        from google_calendar.service import kind
        self.assertEqual(kind(entry), "custom")
        wanted = self.app.extensions["google_calendar"]._wanted({"send": ["custom"], "link_base": ""}, date(2026, 10, 1), date(2026, 10, 2))
        remote = next(w["event"] for w in wanted.values() if w["event"]["summary"].startswith("Office hours"))
        self.assertEqual(remote["summary"], f"Office hours: {name} · Discuss a private concern")
        for secret in (self.alice, "Keep this private"):
            self.assertNotIn(secret, json.dumps(remote))
        item = self.listing("appointment")[0]
        revised = self.client.post("/api/student-support/" + item["id"], json={**item, "state": "attended", "base_revision": 1, "request_id": "attended"}, headers=HEADERS)
        self.assertEqual(revised.status_code, 200, revised.json)
        self.assertEqual(self.notes(), [])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_scores").fetchone()[0], 0)
        stale = self.client.post("/api/student-support/" + item["id"], json={**item, "state": "cancelled", "base_revision": 1, "request_id": "old"}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)

    def test_new_student_reply_reopens_only_its_own_conversation(self):
        self.incoming()
        suggestion = self.listing()[0]
        self.assertFalse(suggestion["id"])
        saved = self.client.post("/api/student-support", json={**suggestion, "state": "resolved", "request_id": "resolved"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertFalse(self.listing()[0]["attention"])
        self.incoming("unrelated", "2026-10-01T14:00:00Z", thread="two")
        tracked = next(i for i in self.listing() if i["id"])
        self.assertEqual(tracked["state"], "resolved")
        self.incoming("m2", "2026-10-01T15:00:00Z")
        tracked = next(i for i in self.listing() if i["id"])
        self.assertEqual((tracked["state"], tracked["saved_state"], tracked["new_reply"]), ("needs_reply", "resolved", True))
        body = {**tracked, "state": "waiting", "base_revision": tracked["revision"], "request_id": "wait"}
        self.incoming("m3", "2026-10-01T16:00:00Z")
        self.assertEqual(self.client.post("/api/student-support/" + tracked["id"], json=body, headers=HEADERS).status_code, 409)
        body["observed"] = next(i for i in self.listing() if i["id"])["observed"]
        self.assertEqual(self.client.post("/api/student-support/" + tracked["id"], json=body, headers=HEADERS).status_code, 200)

    def test_edits_do_not_reopen_and_other_enrollment_cannot_link(self):
        self.incoming()
        item = self.listing()[0]
        self.assertEqual(self.client.post("/api/student-support", json={**item, "enrollment_id": self.bob, "request_id": "wrong"}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/student-support", json={**item, "state": "resolved", "request_id": "ok"}, headers=HEADERS).status_code, 201)
        self.incoming(body="Edited words")
        self.assertEqual(self.listing()[0]["state"], "resolved")
        self.incoming("reply", "2026-10-02T12:00:00Z", author="instructor", thread="other")
        self.assertEqual(len(self.listing()), 1)

    def test_to_do_and_the_next_step_say_what_a_follow_up_waits_on(self):
        # WAITING ON (requirement, 2026-10-04): To do needs attention like a reply owed; the next step is one kept line; only
        # the Follow-up page's read names the instructor's message after the student's latest one.
        self.incoming()
        item = self.listing()[0]
        saved = self.client.post("/api/student-support", json={**item, "state": "todo", "next": "  Ask DR how to book her quizzes  ",
                                 "request_id": "todo"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        tracked = self.listing()[0]
        self.assertEqual((tracked["state"], tracked["attention"], tracked["next"]), ("todo", True, "Ask DR how to book her quizzes"))
        self.incoming("reply", "2026-10-01T13:00:00Z", author="instructor", body="I'll look into it and follow up.")
        self.assertNotIn("answered", self.listing()[0])  # other reads stay byte-identical
        page = self.client.get("/api/student-support", query_string={"kind": "followup", "suggestions": "1"}).json["items"][0]
        with connect(self.app.config["DATABASE"]) as db:
            reply, = db.execute("SELECT id FROM comm_messages WHERE external_id='reply'").fetchone()
        self.assertTrue(page["answered"][0].startswith("2026-10-01T13:00:00"))
        self.assertEqual((page["answered"][1], page["state"]), (reply, "todo"))
        base = {**tracked, "base_revision": tracked["revision"], "observed": page["observed"]}
        for bad in ("Two\nlines", "x" * 201):
            self.assertEqual(self.client.post("/api/student-support/" + tracked["id"], json={**base, "next": bad, "request_id": "bad" + bad[:3]},
                                              headers=HEADERS).status_code, 400)
        cleared = self.client.post("/api/student-support/" + tracked["id"], json={**base, "next": "", "request_id": "clear"}, headers=HEADERS)
        self.assertEqual(cleared.status_code, 200, cleared.json)
        current = self.listing()[0]
        self.assertNotIn("next", current)
        # Saving again without a next step keeps the record as it was (no field, no new revision).
        same = self.client.post("/api/student-support/" + tracked["id"], json={**current, "next": "", "base_revision": current["revision"],
                                "request_id": "same"}, headers=HEADERS)
        self.assertEqual((same.status_code, same.json["revision"]), (200, current["revision"]))
        # A newer student message reopens it as Needs my reply, and nothing of the instructor's is newer any more.
        self.incoming("m2", "2026-10-01T15:00:00Z")
        page = self.client.get("/api/student-support", query_string={"kind": "followup", "suggestions": "1"}).json["items"][0]
        self.assertEqual((page["state"], page["saved_state"], page["new_reply"]), ("needs_reply", "todo", True))
        self.assertNotIn("answered", page)

    def test_a_new_message_you_answered_leaves_the_follow_up_as_you_saved_it(self):
        # ANSWERED (requirement, 2026-10-13): a newer student message the instructor wrote after no longer reopens it, on every read; nothing is saved.
        service, states = self.app.extensions["student_support"], ("resolved", "todo", "waiting")

        def tracked(read, n):
            return next(i for i in read() if i["source_key"] == f"gmail:t{n}")

        for n, state in enumerate(states):
            self.incoming(f"a{n}", "2026-10-01T12:00:00Z", thread=f"t{n}")
            saved = self.client.post("/api/student-support", json={**tracked(self.listing, n), "state": state, "request_id": f"save-{n}"},
                                     headers=HEADERS)
            self.assertEqual(saved.status_code, 201, saved.json)
            self.incoming(f"b{n}", "2026-10-01T15:00:00Z", thread=f"t{n}")  # the student writes again
        for read in (self.listing, self.page):
            for n, state in enumerate(states):
                item = tracked(read, n)
                self.assertEqual((item["state"], item["saved_state"], item["new_reply"], item["attention"]), ("needs_reply", state, True, True))
                self.assertNotIn("new_reply_answered", item)
        self.assertEqual(service.attention()["followup"], 3)
        for n in range(3):
            self.incoming(f"r{n}", "2026-10-01T16:00:00Z", thread=f"t{n}", author="instructor", body="Thanks, that is all I need.")
        for read in (self.listing, self.page):
            for n, state in enumerate(states):
                item = tracked(read, n)
                self.assertEqual((item["state"], item["saved_state"], item["new_reply"], item["new_reply_answered"], item["attention"]),
                                 (state, state, False, True, state == "todo"), state)
        self.assertTrue(tracked(self.page, 0)["answered"][0].startswith("2026-10-01T16:00:00"))
        self.assertNotIn("answered", tracked(self.listing, 0))  # that key is still only the Follow-up page's
        self.assertEqual(service.attention()["followup"], 1)  # the To do: Needs me drops with the other two
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_revisions").fetchone()[0], 3)  # a view, nothing saved
        # She writes again after that answer: it reopens.
        self.incoming("c0", "2026-10-01T17:00:00Z", thread="t0")
        again = tracked(self.listing, 0)
        self.assertEqual((again["state"], again["new_reply"]), ("needs_reply", True))
        self.assertNotIn("new_reply_answered", again)
        # An email written from the follow-up in a thread of its own answers her too (WRITTEN FROM A FOLLOW-UP), once captured.
        self.sent("write-1", "g-new")
        self.assertEqual(self.written("gmail:t0", "write-1").status_code, 200)
        self.assertEqual(tracked(self.listing, 0)["state"], "needs_reply")
        self.incoming("g-new", "2026-10-02T12:00:00Z", thread="g-new", author="instructor", body="Hi Alice")
        for read in (self.listing, self.page):
            item = tracked(read, 0)
            self.assertEqual((item["state"], item["new_reply"], item["new_reply_answered"]), ("resolved", False, True))
        # WRITTEN AT ONCE: mail that Mail accepted seconds ago answers her on the page and in the badge before a round captures it.
        self.incoming("c2", "2026-10-02T11:00:00Z", thread="t2")
        self.assertEqual((tracked(self.page, 2)["state"], service.attention()["followup"]), ("needs_reply", 2))
        self.sent("write-2", "g-two", to="alice@example.test")
        self.assertEqual(self.written("gmail:t2", "write-2").status_code, 200)
        item = tracked(self.page, 2)
        self.assertEqual((item["state"], item["new_reply_answered"], item["answered"]), ("waiting", True, ["2026-10-02T12:00:01.000Z", ""]))
        self.assertEqual(service.attention()["followup"], 1)

    def test_the_page_read_says_who_saved_the_follow_up_last(self):
        # WHAT YOU DID (2026-10-13): the queue's "You updated it" line needs who saved the latest revision; an item's own `actor`
        # is whoever created it. On the Follow-up page's read only (other reads stay byte-identical).
        self.incoming()
        self.assertNotIn("updated_by", self.page()[0])  # a conversation nothing tracks
        created = self.client.post("/api/student-support", json={**self.listing()[0], "state": "todo", "request_id": "todo"}, headers=HEADERS,
                                   environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(created.status_code, 201, created.json)
        [page] = self.page()
        self.assertEqual((page["actor"], page["updated_by"]), ("assistant", "assistant"))
        changed = self.client.post("/api/student-support/" + page["id"], json={**page, "next": "Ask DR", "base_revision": page["revision"],
                                   "request_id": "mine"}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        [page] = self.page()
        self.assertEqual((page["actor"], page["updated_by"]), ("assistant", "workspace"))
        self.assertNotIn("updated_by", self.listing()[0])

    def canvas(self, ident, conversation, at, thread):
        """A student's Canvas message, captured from Canvas and as Canvas's notice email in Gmail `thread`."""
        with connect(self.app.config["DATABASE"]) as db:
            data = {"sent_at": at, "author_role": "student", "author": {"name": "Alice"}, "recipients": [], "subject": "WebAssign",
                    "body_text": "Only 4.1 was moved.", "attachments": []}
            record(db, "canvas_message", ident, {**data, "context": {"conversation_id": conversation}},
                   [(self.alice, self.term, "author", "canvas_student")], conversation)
            record(db, "gmail", "notice-" + ident, {**data, "context": {"canvas_notice": True, "conversation_id": conversation, "thread_id": thread}},
                   [(self.alice, self.term, "author", "canvas_notice")], thread)
            db.commit()

    def page(self):
        response = self.client.get("/api/student-support", query_string={"kind": "followup", "suggestions": "1"})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["items"]

    def test_an_email_reply_in_a_canvas_notice_thread_answers_that_conversation(self):
        # EMAIL REPLY JOINS (requirement, 2026-10-06: a reply Faculty emailed to a Canvas message, since the conversation was a
        # class announcement, left the follow-up looking unanswered; they chose to link it).
        self.canvas("c1", "900", "2026-10-01T12:00:00Z", "n1")
        [item] = self.listing()
        self.assertEqual((item["source_key"], item["state"]), ("canvas:900", "needs_reply"))
        saved = self.client.post("/api/student-support", json={**item, "state": "todo", "request_id": "todo"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.incoming("reply", "2026-10-01T13:00:00Z", thread="n1", author="instructor", body="WebAssign is right; Canvas lags.")
        [tracked] = self.page()  # one conversation, not a second one for the email
        with connect(self.app.config["DATABASE"]) as db:
            reply, = db.execute("SELECT id FROM comm_messages WHERE external_id='reply'").fetchone()
        self.assertEqual((tracked["source_key"], tracked["answered"][1], tracked["state"]), ("canvas:900", reply, "todo"))
        # The same thread names the conversation it already is: a second follow-up for it is refused.
        twice = self.client.post("/api/student-support", json={**item, "source_key": "gmail:n1", "state": "resolved", "request_id": "twice"}, headers=HEADERS)
        self.assertEqual(twice.status_code, 409)
        # The student's answer by email reopens it.
        self.incoming("answer", "2026-10-01T14:00:00Z", thread="n1", body="Thanks, I see it now.")
        [reopened] = self.page()
        self.assertEqual((reopened["state"], reopened["new_reply"]), ("needs_reply", True))
        self.assertNotIn("answered", reopened)

    def sent(self, request_id, message_id, status="accepted", to="alice@tamu.edu"):
        """A new message (its own Gmail thread) and Mail's outcome, as Mail records a send."""
        with connect(self.app.config["DATABASE"]) as db:
            send = str(uuid.uuid4())
            db.execute("INSERT INTO gmail_mail_sends VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (send, request_id, "0" * 64, "me@tamu.edu",
                       json.dumps([to]), "[]", "[]", "DR Quizzes", "Hi Alice", "", "workspace", "2026-10-02T12:00:00+00:00"))
            db.execute("INSERT INTO gmail_mail_outcomes(send_id,status,message_id,thread_id,detail,created_at) VALUES (?,?,?,?,?,?)",
                       (send, status, "" if status == "failed" else message_id, "" if status == "failed" else message_id, "", "2026-10-02T12:00:01+00:00"))
            db.commit()
        return send

    def written(self, source_key, send_request_id, enrollment=None):
        return self.client.post("/api/student-support/followups/written", json={"enrollment_id": enrollment or self.alice,
                                "source_key": source_key, "send_request_id": send_request_id}, headers=HEADERS)

    def comm_id(self, external):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute("SELECT id FROM comm_messages WHERE external_id=?", (external,)).fetchone()[0]

    def test_mail_written_from_a_follow_up_shows_as_its_reply_in_any_thread(self):
        # WRITTEN FROM A FOLLOW-UP (requirement, 2026-10-07, of Fiona Weaver's: an email written with Write to student… went
        # out as a new Gmail thread, so the follow-up kept showing their older reply): it shows once captured; the status stays.
        self.incoming()
        item = self.listing()[0]
        saved = self.client.post("/api/student-support", json={**item, "state": "todo", "next": "Ask DR, then write back", "request_id": "todo"},
                                 headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.incoming("reply", "2026-10-01T13:00:00Z", author="instructor", body="I'll look into it.")
        [page] = self.page()
        self.assertEqual(page["answered"][1], self.comm_id("reply"))
        source = page["source_key"]
        self.sent("write-1", "g-new")
        linked = self.written(source, "write-1")
        self.assertEqual(linked.status_code, 200, linked.json)
        self.assertEqual(self.written(source, "write-1").json, linked.json)  # the same send again: one record
        # Not captured yet, and not to her roster address (WRITTEN AT ONCE): the older reply still shows.
        self.assertEqual(self.page()[0]["answered"][1], self.comm_id("reply"))
        self.incoming("g-new", "2026-10-02T12:00:00Z", thread="g-new", author="instructor", body="Hi Alice")
        [page] = self.page()  # the new thread is not a second follow-up
        self.assertEqual((page["answered"][0][:19], page["answered"][1], page["state"], page["saved_state"], page["revision"]),
                         ("2026-10-02T12:00:00", self.comm_id("g-new"), "todo", "todo", saved.json["revision"]))
        self.assertNotIn("answered", self.listing()[0])  # other reads stay byte-identical
        # Refused: another student's enrollment, an unknown send, a send Gmail refused, and one send for two conversations.
        self.assertEqual(self.written(source, "write-1", enrollment=self.bob).status_code, 400)
        self.assertEqual(self.written(source, "no-such-send").status_code, 404)
        self.sent("write-failed", "", status="failed")
        self.assertEqual(self.written(source, "write-failed").status_code, 400)
        self.incoming("u1", "2026-10-02T13:00:00Z", thread="two", body="Another question")
        other = next(i for i in self.page() if not i["id"])
        self.assertEqual(self.written(other["source_key"], "write-1").status_code, 409)
        # A send captured for someone else never shows here, even when written from this follow-up.
        self.sent("write-bob", "g-bob", to="bob@tamu.edu")
        self.assertEqual(self.written(source, "write-bob").status_code, 200)
        self.incoming("g-bob", "2026-10-02T14:00:00Z", thread="g-bob", author="instructor", enrollment=self.bob)
        self.assertEqual(next(i for i in self.page() if i["id"])["answered"][1], self.comm_id("g-new"))
        # SAME MATTER (2026-10-07): the thread that mail started is part of the conversation it was written from, so a conversation
        # not yet tracked is answered by it, as by a reply in its own thread, and leaves the suggestions.
        self.sent("write-2", "g-two")
        self.assertEqual(self.written(other["source_key"], "write-2").status_code, 200)
        self.incoming("g-two", "2026-10-02T15:00:00Z", thread="g-two", author="instructor")
        self.assertEqual([i for i in self.page() if not i["id"]], [])
        # The student's newer message comes first again: nothing of the instructor's is newer.
        self.incoming("m2", "2026-10-03T12:00:00Z")
        [page] = [i for i in self.page() if i["id"]]
        self.assertEqual((page["state"], page["new_reply"]), ("needs_reply", True))
        self.assertNotIn("answered", page)

    def test_mail_written_from_a_follow_up_shows_at_once_until_the_record_captures_it(self):
        # WRITTEN AT ONCE (requirement, 2026-10-08, of Dana Velez Archer's: their Write to student… email showed on the
        # follow-up only after the next Gmail capture round, minutes later): Mail's own send shows until a round could capture it.
        self.incoming()
        item = self.listing()[0]
        saved = self.client.post("/api/student-support", json={**item, "state": "todo", "request_id": "todo"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.incoming("reply", "2026-10-01T13:00:00Z", author="instructor", body="I'll look into it.")
        source = self.page()[0]["source_key"]

        def gmail_wanted():
            return self.client.get("/api/communications/status").json["channels"]["gmail"]["requested"]

        def last_round(at):
            with connect(self.app.config["DATABASE"]) as db:
                db.execute("UPDATE comm_capture_state SET last_success_at=? WHERE channel='gmail'", (at,))
                db.commit()

        # Never: a send to someone else (even written from here), or one Gmail did not confirm.
        self.assertFalse(gmail_wanted())
        self.sent("write-bob", "g-bob", to="bob@example.test")
        self.assertEqual(self.written(source, "write-bob").status_code, 200)
        self.assertTrue(gmail_wanted())  # a new record asks for a Gmail round now
        self.sent("write-maybe", "", status="uncertain", to="alice@example.test")
        self.assertEqual(self.written(source, "write-maybe").status_code, 200)
        [page] = self.page()
        self.assertEqual(page["answered"][1], self.comm_id("reply"))
        self.assertNotIn("answered_mail", page)
        # Mail accepted it, to her roster address (any spelling): it is her reply now, read from Mail's own send.
        self.sent("write-1", "g-new", to="Alice Example <ALICE@example.test>")
        self.assertEqual(self.written(source, "write-1").status_code, 200)
        [page] = self.page()
        self.assertEqual((page["answered"], page["answered_mail"], page["state"], page["saved_state"], page["revision"]),
                         (["2026-10-02T12:00:01.000Z", ""], {"subject": "DR Quizzes", "text": "Hi Alice"}, "todo", "todo",
                          saved.json["revision"]))
        self.assertEqual(self.client.get("/api/student-support", query_string={"kind": "followup", "suggestions": "1",
                         "enrollment_id": self.alice}).json["items"][0]["answered_mail"]["text"], "Hi Alice")
        self.assertNotIn("answered", self.listing()[0])  # other reads stay byte-identical
        # A round that started after Mail accepted it had its chance: only a captured copy counts from then on.
        last_round("2026-10-02T12:00:05Z")
        self.assertEqual(self.page()[0]["answered"][1], self.comm_id("reply"))
        last_round("2026-10-02T12:00:00.500Z")  # that round started before it: still shown at once
        self.assertEqual(self.page()[0]["answered"][1], "")
        # Captured for her: the record's copy replaces Mail's.
        self.incoming("g-new", "2026-10-02T12:00:02Z", thread="g-new", author="instructor", body="Hi Alice")
        [page] = self.page()
        self.assertEqual((page["answered"][0][:19], page["answered"][1], page["state"]), ("2026-10-02T12:00:02", self.comm_id("g-new"), "todo"))
        self.assertNotIn("answered_mail", page)

    def test_the_students_answer_in_a_thread_written_from_a_follow_up_stays_in_that_follow_up(self):
        # SAME MATTER (requirement, 2026-10-07).
        self.incoming()
        item = self.listing()[0]
        todo = self.client.post("/api/student-support", json={**item, "state": "todo", "next": "Ask DR, then write back", "request_id": "todo"},
                                headers=HEADERS)
        self.assertEqual(todo.status_code, 201, todo.json)
        self.sent("write-1", "g-new")
        self.incoming("g-new", "2026-10-02T12:00:00Z", thread="g-new", author="instructor", body="Hi Alice")
        self.incoming("thanks", "2026-10-02T15:00:00Z", thread="g-new", body="Okay thank you so much!")
        # Before the send was linked (as on 2026-10-07), her answer was a follow-up of its own; it was tracked by itself.
        second = next(i for i in self.listing() if not i["id"])
        self.assertEqual(second["source_key"], "gmail:g-new")
        tracked = self.client.post("/api/student-support", json={**second, "state": "resolved", "title": "DR Quizzes acknowledgment",
                                   "request_id": "second"}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        self.assertEqual(self.written(item["source_key"], "write-1").status_code, 200)
        [one] = self.page()  # one follow-up: the first, which reads her answer (the second keeps its records, out of the list)
        self.assertEqual((one["id"], one["state"], one["saved_state"], one["new_reply"], one["observed"][1]),
                         (todo.json["id"], "needs_reply", "todo", True, self.comm_id("thanks")))
        self.assertEqual(len(self.listing()), 1)
        self.assertEqual(self.client.get("/api/workspace/overview").status_code, 200)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items").fetchone()[0], 2)
        # Tracking the joined thread again is refused: it is already this follow-up.
        again = self.client.post("/api/student-support", json={"kind": "followup", "enrollment_id": self.alice, "source_key": "gmail:g-new",
                                 "observed": one["observed"], "title": "Again", "request_id": "again"}, headers=HEADERS)
        self.assertEqual(again.status_code, 409)
        # Keep separate: the thread is its own follow-up again, with the status it was given.
        apart = self.client.post("/api/student-support/followups/separate", json={"enrollment_id": self.alice, "source_key": "gmail:g-new",
                                 "request_id": "apart"}, headers=HEADERS)
        self.assertEqual(apart.status_code, 200, apart.json)
        found = {i["source_key"]: i for i in self.page()}
        self.assertEqual({k: i["saved_state"] for k, i in found.items()}, {item["source_key"]: "todo", "gmail:g-new": "resolved"})

    def test_the_instructor_joins_two_conversations_and_keeps_them_separate_again(self):
        self.incoming("w1", "2026-10-05T16:47:00Z", thread="week", body="I will miss class today and tomorrow.")
        self.incoming("w2", "2026-10-05T20:41:00Z", thread="week", author="instructor", body="Let me know if you miss more days.")
        week = self.listing()
        self.assertEqual(week, [])  # answered
        self.incoming("f1", "2026-10-06T19:41:00Z", thread="friday", body="I will also miss Friday.")
        self.incoming("o1", "2026-10-06T20:00:00Z", thread="other", enrollment=self.bob)
        [friday] = [i for i in self.listing() if i["enrollment_id"] == self.alice]
        join = lambda **data: self.client.post("/api/student-support/followups/join", json={"enrollment_id": self.alice, "source_key": "gmail:week",
                                               "with_key": "gmail:friday", "request_id": "join", **data}, headers=HEADERS)
        self.assertEqual(join(enrollment_id=self.bob).status_code, 400)  # not Bob's conversations
        self.assertEqual(join(with_key="gmail:other").status_code, 400)  # Bob's conversation is not Alice's
        self.assertEqual(join(with_key="gmail:week").status_code, 409)  # the same conversation
        joined = join()
        self.assertEqual(joined.status_code, 200, joined.json)
        self.assertEqual((joined.json["source_key"], joined.json["into_key"]), ("gmail:friday", "gmail:week"))  # the later joins the earlier
        self.assertEqual(join().json, joined.json)  # idempotent by request ID
        self.assertEqual(join(with_key="gmail:week", source_key="gmail:friday").status_code, 409)  # another decision under that request ID
        [item] = [i for i in self.page() if i["enrollment_id"] == self.alice]
        self.assertEqual((item["source_key"], item["state"], item["observed"][1]), ("gmail:week", "needs_reply", self.comm_id("f1")))
        self.assertEqual([(j["source_key"], j["by"], j["title"]) for j in item["joins"]], [("gmail:friday", "instructor", "Question")])
        self.assertNotIn("joins", [i for i in self.listing() if i["enrollment_id"] == self.alice][0])  # other reads stay byte-identical
        apart = lambda key="gmail:friday", rid="apart": self.client.post("/api/student-support/followups/separate", json={
            "enrollment_id": self.alice, "source_key": key, "request_id": rid}, headers=HEADERS)
        self.assertEqual(apart().status_code, 200)
        self.assertEqual([i["source_key"] for i in self.page() if i["enrollment_id"] == self.alice], [friday["source_key"]])
        self.assertEqual(apart("gmail:friday", "apart-again").status_code, 409)  # nothing joins it now
        with connect(self.app.config["DATABASE"]) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE student_support_conversation_joins SET decision='join'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM student_support_conversation_joins")
        from assistant import policy
        # CONNECT A MESSAGE (requirement, 2026-10-10).
        for endpoint in ("student_support.join", "student_support.separate"):
            rule = policy.base_rule(endpoint)
            self.assertEqual((rule.tier, rule.category, rule.request_id), ("auto", "Follow-up conversations", True), endpoint)
        self.assertEqual(policy.base_rule("student_support.join_preview").tier, "read")
        self.assertEqual(policy.base_rule("student_support.written").tier, "never")

    def test_a_sent_card_is_joined_to_a_follow_up_by_its_message_and_shows_as_your_reply(self):
        # CONNECT A MESSAGE (requirement, 2026-10-10):
        # her Quiz 4 booking card went out as an email of its own, days after the conversation her follow-up tracks.
        self.incoming("w1", "2026-10-02T22:11:00Z", thread="week", body="I missed class with a concussion.")
        self.incoming("w2", "2026-10-05T20:43:00Z", thread="week", author="instructor", body="Send an update after your reassessment.")
        self.incoming("w3", "2026-10-06T00:49:00Z", thread="week", body="I will send you an update.")
        [item] = self.listing()
        saved = self.client.post("/api/student-support", json={**item, "state": "waiting", "title": "Missed class due to concussion",
                                 "next": "Her update after the reassessment", "request_id": "wait"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)

        def card(ident, enrollment=None):
            with connect(self.app.config["DATABASE"]) as db:
                record(db, "gmail", ident, {"sent_at": "2026-10-10T17:13:15Z", "author_role": "instructor", "author": {"name": "Instructor"},
                       "recipients": [], "subject": "IMPORTANT: Book your Quiz 4 makeup for MATH 140", "body_text": "Pick a time on your card.",
                       "attachments": [], "context": {"thread_id": ident}}, [(enrollment or self.alice, self.term, "to", "roster_address")], ident)
                db.commit()
            return self.comm_id(ident)
        mine, bobs = card("card"), card("bob-card", self.bob)
        [before] = [i for i in self.page() if i["enrollment_id"] == self.alice]
        self.assertNotIn("answered", before)  # she wrote last: nothing of the instructor's follows her message yet
        ask = lambda path="/preview", **data: self.client.post("/api/student-support/followups/join" + path, json={
            "enrollment_id": self.alice, "source_key": "gmail:week", "with_message_id": mine, **data}, headers=HEADERS)
        seen = ask()
        self.assertEqual(seen.status_code, 200, seen.json)
        self.assertEqual(seen.json, {
            "source_key": "gmail:card", "into_key": "gmail:week",  # the later conversation is read as part of the earlier one
            "source": {"source_key": "gmail:week", "title": "Question", "started": "2026-10-02T22:11:00.000Z",
                       "followup": {"id": saved.json["id"], "title": "Missed class due to concussion", "state": "waiting"}},
            "with": {"source_key": "gmail:card", "title": "IMPORTANT: Book your Quiz 4 makeup for MATH 140", "started": "2026-10-10T17:13:15.000Z"}})
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_conversation_joins").fetchone()[0], 0)  # a preview writes nothing
        self.assertEqual(ask(with_message_id=bobs).status_code, 400)  # Bob's email is not in Alice's record
        self.assertEqual(ask(with_message_id="nothing").status_code, 400)
        self.assertEqual(ask(with_key="gmail:card").status_code, 400)  # a key or a message, not both
        self.assertEqual(ask(with_message_id=self.comm_id("w2")).status_code, 409)  # already in this conversation
        self.assertEqual(ask(request_id="extra").status_code, 400)  # the preview takes no request
        self.assertEqual(ask("", request_id="bob", with_message_id=bobs).status_code, 400)
        joined = ask("", request_id="connect")
        self.assertEqual(joined.status_code, 200, joined.json)
        self.assertEqual((joined.json["source_key"], joined.json["into_key"], joined.json["decision"]), ("gmail:card", "gmail:week", "join"))
        self.assertEqual(ask("", request_id="connect").json, joined.json)  # idempotent by request ID
        [after] = [i for i in self.page() if i["enrollment_id"] == self.alice]
        # One follow-up, its status and next step as saved; the card's email is the instructor's latest reply there.
        self.assertEqual((after["id"], after["state"], after["next"], after["new_reply"]),
                         (saved.json["id"], "waiting", "Her update after the reassessment", False))
        self.assertEqual(after["answered"][1], mine)
        self.assertEqual([(j["source_key"], j["by"], j["title"]) for j in after["joins"]],
                         [("gmail:card", "instructor", "IMPORTANT: Book your Quiz 4 makeup for MATH 140")])
        self.assertEqual(ask().status_code, 409)  # already one follow-up
        # Her answer in the card's own thread arrives in that follow-up, not as a new one.
        with connect(self.app.config["DATABASE"]) as db:
            record(db, "gmail", "card-answer", {"sent_at": "2026-10-10T19:00:00Z", "author_role": "student", "author": {"name": "Alice"},
                   "recipients": [], "subject": "Re: IMPORTANT: Book your Quiz 4 makeup for MATH 140", "body_text": "None of these work for me.",
                   "attachments": [], "context": {"thread_id": "card"}}, [(self.alice, self.term, "author", "roster_address")], "card")
            db.commit()
        [reopened] = [i for i in self.page() if i["enrollment_id"] == self.alice]
        self.assertEqual((reopened["id"], reopened["state"], reopened["new_reply"], reopened["observed"][1]),
                         (saved.json["id"], "needs_reply", True, self.comm_id("card-answer")))

    def test_mail_in_a_follow_up_that_no_longer_waits_for_a_reply_is_settled(self):
        # MAIL-FOLLOW-UP (requirement, 2026-10-10): what Mail's Students list asks about the
        # student's message that ends a thread.
        settled = self.app.extensions["student_support"].settled_mail
        self.incoming()
        self.assertEqual(settled([(self.alice, "m1")]), set())  # a suggestion nothing tracks is not a follow-up
        item = self.listing()[0]
        saved = self.client.post("/api/student-support", json={**item, "state": "resolved", "request_id": "resolved"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(settled([(self.alice, "m1"), (self.alice, "m1")]), {"m1"})
        self.assertEqual(settled([(self.bob, "m1")]), set())  # another student's follow-up is never asked about it
        self.assertEqual(settled([(self.alice, "never-captured"), ("", "m1"), (self.alice, "")]), set())
        self.assertEqual(settled([]), set())
        tracked = self.listing()[0]
        for index, state in enumerate(("waiting", "todo", "snoozed", "needs_reply", "resolved")):
            body = {**tracked, "state": state, "date": "2026-10-20" if state == "snoozed" else "", "base_revision": tracked["revision"],
                    "request_id": f"state-{index}"}
            changed = self.client.post("/api/student-support/" + tracked["id"], json=body, headers=HEADERS)
            self.assertEqual(changed.status_code, 200, changed.json)
            tracked = next(i for i in self.listing() if i["id"])
            self.assertEqual(settled([(self.alice, "m1")]), set() if state == "needs_reply" else {"m1"}, state)
        # A newer message reopens the follow-up: its mail is the instructor's to answer again, in the same thread or another.
        self.incoming("m2", "2026-10-01T15:00:00Z")
        self.assertEqual(self.listing()[0]["state"], "needs_reply")
        self.assertEqual(settled([(self.alice, "m2"), (self.alice, "m1")]), set())
        # Mail in another of the student's conversations is not this follow-up's ...
        self.incoming("f1", "2026-10-02T09:00:00Z", thread="friday", body="I will also miss Friday.")
        reopened = next(i for i in self.listing() if i["id"])
        again = self.client.post("/api/student-support/" + reopened["id"], json={**reopened, "state": "resolved", "base_revision": reopened["revision"],
                                 "request_id": "again"}, headers=HEADERS)
        self.assertEqual(again.status_code, 200, again.json)
        self.assertEqual(settled([(self.alice, "m2"), (self.alice, "f1")]), {"m2"})
        # ... until it is joined to it (SAME MATTER): then it reopens it, and is settled with it.
        joined = self.client.post("/api/student-support/followups/join", json={"enrollment_id": self.alice, "source_key": "gmail:one",
                                  "with_key": "gmail:friday", "request_id": "join"}, headers=HEADERS)
        self.assertEqual(joined.status_code, 200, joined.json)
        self.assertEqual(settled([(self.alice, "m2"), (self.alice, "f1")]), set())
        whole = next(i for i in self.listing() if i["id"])
        closed = self.client.post("/api/student-support/" + whole["id"], json={**whole, "state": "resolved", "base_revision": whole["revision"],
                                  "request_id": "closed"}, headers=HEADERS)
        self.assertEqual(closed.status_code, 200, closed.json)
        self.assertEqual(settled([(self.alice, "m2"), (self.alice, "f1")]), {"m2", "f1"})
        # The instructor's own message is nobody's to settle, and nothing was saved by asking.
        self.incoming("reply", "2026-10-02T10:00:00Z", author="instructor")
        self.assertEqual(settled([(self.alice, "reply")]), set())
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items").fetchone()[0], 1)

    def test_a_canvas_notice_in_a_settled_follow_up_is_settled_by_its_email(self):
        # MAIL-FOLLOW-UP: Canvas's notice email is the message Mail lists; the follow-up is the Canvas conversation it names.
        settled = self.app.extensions["student_support"].settled_mail
        self.canvas("c1", "900", "2026-10-01T12:00:00Z", "n1")
        [item] = self.listing()
        self.assertEqual(settled([(self.alice, "notice-c1")]), set())
        saved = self.client.post("/api/student-support", json={**item, "state": "waiting", "request_id": "waiting"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(settled([(self.alice, "notice-c1")]), {"notice-c1"})
        self.canvas("c2", "900", "2026-10-01T14:00:00Z", "n1")  # the student writes again in Canvas
        self.assertEqual(settled([(self.alice, "notice-c2"), (self.alice, "notice-c1")]), set())
        # PERFORMANCE: only this student's conversations are read, never the term's.
        from student_support import triage
        with patch.object(triage, "snapshots", wraps=triage.snapshots) as read, patch("jev.client.available", return_value=True):
            settled([(self.alice, "notice-c2"), (self.bob, "notice-c2")])
        self.assertEqual([call.args[1] for call in read.call_args_list], [self.alice])

    def test_a_thread_whose_notices_name_two_conversations_joins_neither(self):
        self.canvas("c1", "901", "2026-10-01T12:00:00Z", "shared")
        self.canvas("c2", "902", "2026-10-01T12:05:00Z", "shared")
        self.incoming("reply", "2026-10-01T13:00:00Z", thread="shared", author="instructor")
        found = {i["source_key"]: i for i in self.page()}
        self.assertEqual(set(found), {"canvas:901", "canvas:902"})  # still unanswered; the email stays its own conversation
        self.assertTrue(all(i["state"] == "needs_reply" and "answered" not in i for i in found.values()))

    def test_a_follow_up_saved_under_the_gmail_thread_reads_its_canvas_conversation(self):
        # Records keep the key they were saved with (Fiona Weaver's, 2026-10-04, is `gmail:<thread>`).
        self.incoming("a1", "2026-10-01T11:00:00Z", thread="n1", body="Section 515?")
        [item] = self.listing()
        saved = self.client.post("/api/student-support", json={**item, "state": "waiting", "request_id": "wait"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.canvas("c1", "903", "2026-10-01T12:00:00Z", "n1")  # Canvas's notice lands in the same Gmail thread
        [tracked] = self.listing()  # no second, untracked suggestion for the Canvas conversation
        self.assertEqual((tracked["source_key"], tracked["state"], tracked["new_reply"]), ("gmail:n1", "needs_reply", True))
        with connect(self.app.config["DATABASE"]) as db:
            notice, = db.execute("SELECT id FROM comm_messages WHERE external_id='notice-c1'").fetchone()
            canvas, = db.execute("SELECT id FROM comm_messages WHERE external_id='c1'").fetchone()
        self.assertIn(tracked["observed"][1], (notice, canvas))
        revised = self.client.post("/api/student-support/" + tracked["id"], json={**tracked, "state": "resolved",
                                   "base_revision": tracked["revision"], "request_id": "resolve"}, headers=HEADERS)
        self.assertEqual(revised.status_code, 200, revised.json)
        self.assertEqual(self.listing()[0]["state"], "resolved")

    def test_invalid_inputs_and_home_has_counts_only(self):
        for change in ({"date": "2026-02-30"}, {"time": "25:00"}, {"end_time": "13:00"}, {"title": ""}, {"state": "excused"}, {"kind": []}):
            self.assertEqual(self.save_support(**change).status_code, 400)
        self.incoming()
        overview = self.client.get("/api/workspace/overview").json
        for secret in (self.alice, "Alice", "Question"):
            self.assertNotIn(secret, json.dumps(overview))
        self.assertTrue(any(i["key"] == "support:followup" for i in overview["attention"]))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items").fetchone()[0], 0)

    def test_assistant_writes_require_approval_and_keep_the_actor(self):
        from assistant import policy
        for endpoint in ("student_support.create", "student_support.revise"):
            self.assertEqual(policy.base_rule(endpoint).tier, "approve", endpoint)
        answer = self.client.post("/api/student-support", json={"request_id": "assistant-followup",
            "kind": "followup", "enrollment_id": self.alice, "title": "Check in"}, headers=HEADERS,
            environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(answer.status_code, 201, answer.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT actor FROM student_support_items").fetchone()[0], "assistant")
            self.assertEqual(db.execute("SELECT actor FROM student_support_revisions").fetchone()[0], "assistant")

    def test_start_now_clears_old_work_and_late_imports_but_keeps_new_activity(self):
        self.incoming(at="2026-10-01T09:00:00Z")
        old = self.listing()[0]
        with patch("student_support.service.timestamp", return_value="2026-10-01T10:00:00+00:00"):
            tracked = self.client.post("/api/student-support", json={**old, "state": "waiting", "request_id": "track"}, headers=HEADERS)
            self.assertEqual(tracked.status_code, 201, tracked.json)
            manual = self.client.post("/api/student-support", json={"kind": "followup", "enrollment_id": self.alice,
                "title": "Old commitment", "request_id": "manual"}, headers=HEADERS)
            self.assertEqual(manual.status_code, 201, manual.json)
            self.assertEqual(self.save_support().status_code, 201)
        self.incoming("old-untracked", "2026-10-01T11:00:00Z", thread="two")
        self.incoming("new-message", "2026-10-01T12:01:00Z", thread="new")
        appointments = self.listing("appointment")
        plan = self.reset_preview()
        self.assertEqual((plan["cleared"], plan["tracked"], plan["suggestions"]), (3, 2, 1))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_baselines").fetchone()[0], 0)
            messages = [tuple(row) for row in db.execute("SELECT * FROM comm_revisions ORDER BY id")]
        body = {"through": plan["through"], "fingerprint": plan["fingerprint"], "confirmation": "start-now", "request_id": "start-now"}
        result = self.client.post(self.RESET, json=body, headers=HEADERS, environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(len(result.json["resolved"]), 2)
        self.assertEqual([i["source_key"] for i in self.listing() if i["attention"]], ["gmail:new"])
        self.assertEqual(self.listing("appointment"), appointments)
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.client.get("/api/student-support").json["tracking_since"], plan["through"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(messages, [tuple(row) for row in db.execute("SELECT * FROM comm_revisions ORDER BY id")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_scores").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT actor FROM student_support_baselines").fetchone()[0], "assistant")
        # Historical messages first captured after the reset stay out, including
        # a newer pre-cutoff reply in the previously tracked conversation.
        self.incoming("late-old", "2026-10-01T11:30:00Z", thread="late")
        self.incoming("late-old-reply", "2026-10-01T11:45:00Z")
        self.assertEqual([i["source_key"] for i in self.listing() if i["attention"]], ["gmail:new"])
        self.incoming("real-new-reply", "2026-10-01T12:02:00Z")
        self.assertEqual({i["source_key"] for i in self.listing() if i["attention"]}, {"gmail:new", "gmail:one"})
        # Replaying the exact reset is a receipt lookup; it cannot clear the new reply.
        self.assertEqual(self.client.post(self.RESET, json=body, headers=HEADERS).json, result.json)
        self.assertEqual(sum(i["attention"] for i in self.listing()), 2)
        self.assertEqual(self.client.post(self.RESET, json={**body, "through": "2026-10-01T12:03:00Z"}, headers=HEADERS).status_code, 409)
        added = self.client.post("/api/student-support", json={"kind": "followup", "enrollment_id": self.bob,
            "title": "New commitment", "request_id": "new-manual"}, headers=HEADERS)
        self.assertEqual(added.status_code, 201, added.json)
        self.assertEqual(sum(i["attention"] for i in self.listing()), 3)

    def test_start_now_requires_an_exact_current_preview_and_retains_its_receipt(self):
        from assistant import policy
        self.incoming(at="2026-10-01T10:00:00Z")
        plan = self.reset_preview()
        body = {"through": plan["through"], "fingerprint": plan["fingerprint"], "confirmation": "start-now", "request_id": "reset"}
        self.assertEqual(self.client.post(self.RESET, json={**body, "confirmation": ""}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post(self.RESET, json=body).status_code, 403)
        self.incoming("another-old", "2026-10-01T11:00:00Z", thread="two")
        self.assertEqual(self.client.post(self.RESET, json=body, headers=HEADERS).status_code, 409)
        plan = self.reset_preview()
        result = self.client.post(self.RESET, json={**body, "fingerprint": plan["fingerprint"]}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.listing(), [])
        self.assertEqual(self.app.extensions["student_support"].attention()["followup"], 0)
        overview = self.client.get("/api/workspace/overview").json
        self.assertFalse(any(item["key"] == "support:followup" for item in overview["attention"]))
        backwards = self.client.post(self.RESET + "/preview", json={"through": "2026-10-01T11:00:00Z"}, headers=HEADERS)
        self.assertEqual(backwards.status_code, 400)
        with connect(self.app.config["DATABASE"]) as db:
            for statement in ("DELETE FROM student_support_baselines", "UPDATE student_support_baselines SET actor='other'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
        rule = policy.base_rule("student_support.start_now")
        self.assertEqual((rule.tier, rule.preview, rule.confirmation), ("approve", "student_support.start_now_preview", "start-now"))
        self.assertEqual(policy.base_rule("student_support.start_now_preview").tier, "read")

    def test_start_now_rejects_invalid_and_future_cutoffs(self):
        for through in (None, [], "no date", "2026-10-01T12:00:00", "2999-01-01T00:00:00Z"):
            with self.subTest(through=through):
                result = self.client.post(self.RESET + "/preview", json={"through": through}, headers=HEADERS)
                self.assertEqual(result.status_code, 400, result.json)
