"""Messages to chosen sections through Canvas (AGENTS.md "Canvas" MESSAGES, canvas/README.md "Messaging sections") offline,
against the loopback FakeCanvas: who a message reaches and why the rest do not, one conversation per student, the receipts
written before Canvas is asked, no second send, and the delivery check. Nothing here reaches Canvas."""
import json
import signal
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fake_canvas import now as fake_now
from test_canvas import ALICE, BOB, CAROL, Seeded
from test_storage import HEADERS

from assistant import policy
from assistant.conversations import ATTACHED
from canvas import messages, stopping, web
from rosters import connect

DANA = "999888777"
BASE = "/api/canvas/messages"


class MessageTests(Seeded):
    def setUp(self):
        super().setUp()
        self.keep()

    def link(self, number, course):
        route = f"/api/canvas/sections/{self.sections[number]}"
        answer = self.client.post(route, json={"course_id": course, "base": ""}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)

    def course(self, name, students):
        """A Canvas course with students [(name, sis, state)]: {name: Canvas user ID}."""
        course = self.fake.add_course(name, name, "Fall 2026")
        section = self.fake.add_section(course, name)
        return course, {who: self.fake.add_student(course, section, who, sis, state=state) for who, sis, state in students}

    def ask(self, path, body, method="post"):
        return getattr(self.client, method)(path, json=body, headers=HEADERS)

    def settled(self, answer):
        """The record once the background send has finished (the send answers at once)."""
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertTrue(self.app.extensions["canvas_messages"].wait_idle(10))
        found = self.client.get(f"{BASE}/{answer.json['id']}")
        self.assertEqual(found.status_code, 200, found.json)
        return found.json

    def rows(self, table):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def test_a_message_reaches_the_ticked_section_as_one_group_conversation(self):
        """requirement, 2026-10-01: group send by default, like Canvas's inbox with "Send an individual message" unticked."""
        a, users = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "active"),
                                                ("Newcomer, Dana", DANA, "active"), ("Invited, Eli", "444555666", "invited")])
        b, _ = self.course("MATH-140-508", [("Example, Carol", CAROL, "active")])
        self.link(507, a)
        self.link(508, b)
        before = len(self.fake.requests)
        view = self.client.get(BASE).json
        self.assertEqual(len(self.fake.requests), before)  # Faculty's records only
        self.assertEqual([(s["number"], s["students"], s["course"]["course_id"]) for s in view["terms"][0]["sections"]], [(507, 2, a), (508, 1, b)])
        self.assertEqual(view["sent"], [])

        body = {"sections": [self.sections[507]], "subject": "  Exam 1   room  ", "body": "Exam 1 is in HECC 207.\r\nBring a pencil.\n"}
        preview = self.ask(f"{BASE}/preview", body)
        self.assertEqual(preview.status_code, 200, preview.json)
        found = preview.json
        self.assertEqual((found["subject"], found["body"], found["recipients"]), ("Exam 1 room", "Exam 1 is in HECC 207.\nBring a pencil.", 3))
        self.assertEqual(found["courses"], [{"course_id": a, "name": "MATH-140-507", "sections": [507], "recipients": 3, "only_canvas": 1,
                                              "kind": "group", "too_many_for_group": False, "roster": 2, "roster_reached": 2,
                                              "students": ["Example, Alice", "Example, Bob", "Newcomer, Dana"]}])
        self.assertEqual(found["individual"], False)
        self.assertEqual(found["left_out"], [{"name": "Invited, Eli", "reason": "Canvas lists the enrollment as invited", "course": "MATH-140-507"}])
        self.assertEqual((found["not_reached"], found["unlinked"]), ([], []))
        text = json.dumps(found)
        for secret in (ALICE, BOB, DANA, *users.values()):
            self.assertNotIn(secret, text)  # neither a UIN nor a Canvas user ID leaves the server
        self.assertFalse([r for r in self.fake.requests if r["method"] == "POST"])

        send = {**body, "fingerprint": found["fingerprint"], "request_id": "msg-1", "confirmation": "send"}
        self.assertEqual(self.client.post(f"{BASE}/send", json=send).status_code, 403)  # the workspace guard
        self.assertEqual(self.ask(f"{BASE}/send", {**send, "confirmation": ""}).status_code, 400)
        self.assertEqual(self.ask(f"{BASE}/send", {**send, "fingerprint": "0" * 64}).status_code, 409)
        self.assertEqual(self.rows("canvas_messages"), [])
        record = self.settled(self.ask(f"{BASE}/send", send))
        self.assertEqual((record["status"], record["recipients"], record["courses"][0]["conversations"], record["courses"][0]["sections"],
                          record["courses"][0]["kind"]), ("accepted", 3, 1, [507], "group"))
        [posted] = self.fake.sent_messages
        self.assertEqual(sorted(posted["recipients"]), sorted([users["Example, Alice"], users["Example, Bob"], users["Newcomer, Dana"]]))
        self.assertEqual(posted, {"recipients": posted["recipients"], "subject": "Exam 1 room", "body": "Exam 1 is in HECC 207.\nBring a pencil.",
                                  "context_code": f"course_{a}", "group_conversation": True})
        [conversation] = self.fake.conversations.values()  # ONE conversation everyone shares
        self.assertEqual(len(conversation["participants"]), 4)

        again = self.ask(f"{BASE}/send", send)  # the same request ID: what happened, never a second message
        self.assertEqual((again.status_code, again.json["id"]), (200, record["id"]))
        self.assertEqual(len(self.fake.sent_messages), 1)
        self.assertEqual(self.ask(f"{BASE}/send", {**send, "fingerprint": "f" * 64}).status_code, 409)

        delivery = self.client.get(f"{BASE}/{record['id']}/delivery").json["delivery"]
        self.assertEqual([(d["state"], d["delivered"], d["missing"]) for d in delivery], [("delivered", 3, [])])
        listed = self.client.get(BASE).json["sent"]
        self.assertEqual([(m["subject"], m["status"], m["recipients"]) for m in listed], [("Exam 1 room", "accepted", 3)])
        [message] = self.rows("canvas_messages")
        self.assertEqual(json.loads(message["sections_json"]), [self.sections[507]])
        [delivered] = self.rows("canvas_message_deliveries")
        self.assertEqual([r["name"] for r in json.loads(delivered["recipients_json"])], ["Example, Alice", "Example, Bob", "Newcomer, Dana"])
        with connect(self.app.config["DATABASE"]) as db:
            for sql in ("UPDATE canvas_messages SET subject='x'", "DELETE FROM canvas_messages", "UPDATE canvas_message_deliveries SET mode='async'",
                        "DELETE FROM canvas_message_outcomes"):
                with self.subTest(sql=sql), self.assertRaisesRegex(Exception, "append-only|retained"):
                    db.execute(sql)

    def test_individual_messages_give_each_student_their_own_conversation(self):
        a, users = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "active")])
        self.link(507, a)
        body = {"sections": [self.sections[507]], "subject": "Your grade", "body": "Check your grade.", "individual": True}
        found = self.ask(f"{BASE}/preview", body).json
        self.assertEqual((found["individual"], found["courses"][0]["kind"]), (True, "individual"))
        group = self.ask(f"{BASE}/preview", {**body, "individual": False}).json
        self.assertNotEqual(group["fingerprint"], found["fingerprint"])  # how it is sent is part of what is approved
        self.assertEqual(self.ask(f"{BASE}/send", {**body, "fingerprint": group["fingerprint"], "request_id": "one-1", "confirmation": "send"}).status_code, 409)
        record = self.settled(self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": "one-2", "confirmation": "send"}))
        self.assertEqual((record["status"], record["courses"][0]["kind"], record["courses"][0]["conversations"]), ("accepted", "individual", 2))
        [posted] = self.fake.sent_messages
        self.assertEqual({k: posted[k] for k in ("group_conversation", "bulk_message", "mode")}, {"group_conversation": True, "bulk_message": True, "mode": "sync"})
        self.assertEqual(sorted(len(c["participants"]) for c in self.fake.conversations.values()), [2, 2])  # one conversation per student
        delivery = self.client.get(f"{BASE}/{record['id']}/delivery").json["delivery"]
        self.assertEqual((delivery[0]["state"], delivery[0]["delivered"]), ("delivered", 2))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT kind FROM canvas_message_deliveries")], ["individual"])
        self.assertEqual(self.ask(f"{BASE}/preview", {**body, "individual": "yes"}).status_code, 400)

    def test_a_combined_course_reaches_only_the_ticked_section_and_nobody_is_guessed(self):
        c, _ = self.course("MATH-140-507/508", [("Example, Alice", ALICE, "active"), ("Example, Carol", CAROL, "active"),
                                                 ("Newcomer, Dana", DANA, "active")])
        self.link(507, c)
        self.link(508, c)
        body = {"sections": [self.sections[507]], "subject": "Quiz", "body": "Quiz on Friday."}
        found = self.ask(f"{BASE}/preview", body).json
        self.assertEqual(found["courses"][0]["students"], ["Example, Alice"])
        self.assertEqual({(x["name"], x["reason"]) for x in found["left_out"]}, {
            ("Example, Carol", "on Section 508’s roster, which you did not choose"),
            ("Newcomer, Dana", "on no roster here, in a Canvas course shared with Section 508, which you did not choose")})
        self.assertEqual(found["not_reached"], [{"name": "Example, Bob", "section": 507, "reason": "not in the section’s Canvas course"}])
        self.assertEqual((found["courses"][0]["roster"], found["courses"][0]["roster_reached"]), (2, 1))  # Bob is 507's, and is not reached
        both = self.ask(f"{BASE}/preview", {**body, "sections": [self.sections[507], self.sections[508]]}).json
        self.assertEqual((both["courses"][0]["sections"], both["courses"][0]["students"]), ([507, 508], ["Example, Alice", "Example, Carol", "Newcomer, Dana"]))
        self.assertEqual((both["courses"][0]["roster"], both["courses"][0]["roster_reached"]), (3, 2))
        # A student dropped in Faculty is left out, with the reason.
        group = self.client.get("/api/state").json["course_groups"][0]
        roster = self.client.get(f"/api/course-groups/{group['id']}/sections/507/roster").json
        alice = next(s["id"] for s in roster["students"] if "Alice" in json.dumps(s))
        dropped = self.ask(f"/api/course-groups/{group['id']}/sections/507/roster/drop", {"enrollment_ids": [alice], "request_id": "drop-1", "source": "canvas", "reason": "Left"})
        self.assertEqual(dropped.status_code, 200, dropped.json)
        later = self.ask(f"{BASE}/preview", {**body, "sections": [self.sections[507], self.sections[508]]}).json
        self.assertEqual(later["courses"][0]["students"], ["Example, Carol", "Newcomer, Dana"])
        self.assertEqual((later["courses"][0]["roster"], later["courses"][0]["roster_reached"]), (2, 1))  # a dropped student is not the roster's
        self.assertIn({"name": "Example, Alice", "reason": "dropped from Section 507 in Faculty", "course": "MATH-140-507/508"}, later["left_out"])

    def test_a_student_in_two_courses_gets_one_message_in_their_own_section_s_course(self):
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Carol", CAROL, "active")])
        b, users = self.course("MATH-140-508", [("Example, Carol", CAROL, "active"), ("Example, Alice", ALICE, "inactive")])
        self.link(507, a)
        self.link(508, b)
        body = {"sections": [self.sections[507], self.sections[508]], "subject": "Hello", "body": "Welcome."}
        found = self.ask(f"{BASE}/preview", body).json
        self.assertEqual([(c["course_id"], c["students"]) for c in found["courses"]], [(a, ["Example, Alice"]), (b, ["Example, Carol"])])
        self.assertEqual(found["recipients"], 2)
        self.assertEqual(found["left_out"], [])
        self.assertEqual([(c["roster"], c["roster_reached"]) for c in found["courses"]], [(2, 1), (1, 1)])

    def test_all_students_in_a_section_is_said_only_when_every_current_student_is_reached(self):
        """requirement, 2026-10-12, of the Assistant's card for a message to every section: "Can it simply says All Students In 507,
        All Students in 508, etc". Each course says how many current students its ticked sections have and how many get it."""
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "invited"), ("Newcomer, Dana", DANA, "active")])
        b, _ = self.course("MATH-140-508", [("Example, Carol", CAROL, "active")])
        self.link(507, a)
        self.link(508, b)
        body = {"sections": [self.sections[507], self.sections[508]], "subject": "Quiz", "body": "Bring the take-home quiz.\n" + "x" * 7000}
        found = self.ask(f"{BASE}/preview", body).json
        # 507: Bob's Canvas enrollment is only invited, so one of its two is not reached; someone only Canvas lists is no part of either count.
        self.assertEqual([(c["sections"], c["roster"], c["roster_reached"], c["recipients"], c["only_canvas"]) for c in found["courses"]],
                         [([507], 2, 1, 2, 1), ([508], 1, 1, 1, 0)])
        self.assertEqual((found["not_reached"], [x["name"] for x in found["left_out"]]), ([], ["Example, Bob"]))
        # The counts are no part of what is approved: a review made before they existed still sends.
        bare = [{k: v for k, v in c.items() if k not in ("roster", "roster_reached")} for c in self.app.extensions["canvas_messages"].plan(body)["courses"]]
        self.assertEqual(found["fingerprint"], messages.fingerprint(found["subject"], found["body"], bare))
        # The Assistant's card is drawn from this review, with the message's own words whole (never the model-sized excerpt).
        assistant = self.app.extensions["assistant"]
        asked, _ = assistant.run_tool(assistant.open_session("test"), "faculty_write", {
            "method": "POST", "path": f"{BASE}/send", "summary": "Remind both sections about the quiz", "body": body})
        self.assertEqual(json.loads(asked)["outcome"], "waiting_for_approval", asked)
        card, = self.client.get("/api/assistant/actions").json["pending"]
        shown = card["preview"]["data"]
        self.assertEqual((card["endpoint"], shown["body"], shown["subject"]), ("canvas.message_send", found["body"], "Quiz"))
        self.assertEqual([(c["roster"], c["roster_reached"]) for c in shown["courses"]], [(2, 1), (1, 1)])
        self.assertEqual(self.fake.sent_messages, [])

    def test_the_assistant_ticks_individual_only_on_the_instructor_s_own_words(self):
        """The model's own tick is taken off before the review; their words in the chat keep it."""
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "active")])
        self.link(507, a)
        body = {"sections": [self.sections[507]], "subject": "Quiz", "body": "Bring the take-home quiz.", "individual": True}
        group = self.ask(f"{BASE}/preview", {**body, "individual": False}).json["fingerprint"]
        assistant = self.app.extensions["assistant"]

        def prepared(said):
            session = assistant.open_session("test")
            chat = SimpleNamespace(session=session, wait_for_action=lambda ident: None,
                                   transcript=[("Instructor", text) for text in said] + [("Faculty", "Send it individually.")])
            with patch.object(assistant.conversations, "by_session", return_value=chat):
                # A fingerprint the model read from its own review of the ticked message is never what is approved.
                answer, _ = assistant.run_tool(session, "faculty_write", {"method": "POST", "path": f"{BASE}/send", "summary": "Remind 507",
                                                                         "body": {**body, "fingerprint": "0" * 64}})
            answer = json.loads(answer)
            return answer, next(c for c in self.client.get("/api/assistant/actions").json["pending"] if c["id"] == answer.get("action_id"))

        asked = "Send a Canvas message to all my sections: bring the take-home quiz."
        # Not theirs to tick it: no such word, a word from before their latest three messages, or a word in an attached file's name.
        for said in ([asked], ["Send it individually."] + [asked] * 3, [asked + ATTACHED + "Individual scores.pdf at /api/assistant/uploads/1)"]):
            answer, card = prepared(said)
            self.assertEqual(answer["outcome"], "waiting_for_approval", answer)
            self.assertIn("`individual` was removed", answer["note"])
            self.assertEqual((card["body"]["individual"], card["preview"]["data"]["individual"], card["preview"]["data"]["courses"][0]["kind"],
                              card["preview"]["fingerprint"]), (False, False, "group", group))
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertTrue(self.app.extensions["canvas_messages"].wait_idle(10))
        self.assertEqual([(m["group_conversation"], "bulk_message" in m) for m in self.fake.sent_messages], [(True, False)])
        for words in ("Send each student an individual message about the quiz.", "send it to 507 individually", "Separate messages, please.",
                      "Each student should get their own conversation."):
            answer, card = prepared([asked, words])
            self.assertNotIn("removed", answer["note"])
            self.assertEqual((card["body"]["individual"], card["preview"]["data"]["courses"][0]["kind"]), (True, "individual"), words)
        self.assertEqual(len(self.fake.sent_messages), 1)

    def test_delivery_is_the_message_itself_not_an_earlier_one_of_the_same_subject(self):
        """2026-10-01 audit: subject and time alone said an unsent message had gone, because an earlier message of the same subject
        had (and a later reply in its conversation moved that conversation's time past the send)."""
        a, users = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "active")])
        self.link(507, a)

        def send(text, request_id):
            body = {"sections": [self.sections[507]], "subject": "Exam 1", "body": text, "individual": True}
            found = self.ask(f"{BASE}/preview", body).json
            return self.settled(self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": request_id, "confirmation": "send"}))

        first = send("Exam 1 is in HECC 207.", "first-1")
        self.fake.message_answer = (502, {"errors": [{"message": "bad gateway"}]})
        second = send("Exam 1 moved to HECC 100.", "second-1")  # Canvas never made it
        self.fake.message_answer = None
        self.assertEqual(second["status"], "uncertain")
        alice = next(c for c in self.fake.conversations.values() if users["Example, Alice"] in c["participants"])
        self.fake.add_message(alice["id"], self.fake.user["id"], "See you there.", fake_now())

        def delivery(record):
            found = self.client.get(f"{BASE}/{record['id']}/delivery").json["delivery"][0]
            return found["state"], found["delivered"], found["missing"]

        self.assertEqual(delivery(second), ("none", 0, ["Example, Alice", "Example, Bob"]))
        self.assertEqual(delivery(first), ("delivered", 2, []))
        self.assertEqual(self.fake.marked_read, [])  # reading the messages never marks them read

    def test_more_than_a_hundred_go_individually_as_canvas_s_background_batch_and_delivery_is_checked(self):
        """Canvas allows at most 100 people in a group conversation: a bigger course is sent individually, and the review says so."""
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "active"), ("Newcomer, Dana", DANA, "active")])
        self.link(507, a)
        body = {"sections": [self.sections[507]], "subject": "Big", "body": "Many students."}
        with patch.object(web, "SYNC_MAX", 2):
            found = self.ask(f"{BASE}/preview", body).json
            self.assertEqual((found["individual"], found["courses"][0]["kind"], found["courses"][0]["too_many_for_group"], found["group_limit"]),
                             (False, "individual", True, 2))
            record = self.settled(self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": "big-1", "confirmation": "send"}))
        self.assertEqual({k: self.fake.sent_messages[0][k] for k in ("bulk_message", "mode")}, {"bulk_message": True, "mode": "async"})
        self.assertEqual((record["status"], record["courses"][0]["conversations"], record["courses"][0]["batch"]), ("accepted", None, True))
        delivery = self.client.get(f"{BASE}/{record['id']}/delivery").json["delivery"]
        self.assertEqual((delivery[0]["state"], delivery[0]["completion"]), ("sending", 0.5))
        self.fake.finish_batches()
        delivery = self.client.get(f"{BASE}/{record['id']}/delivery").json["delivery"]
        self.assertEqual((delivery[0]["state"], delivery[0]["delivered"]), ("delivered", 3))

    def test_refusals_failures_and_no_answer_are_recorded_and_never_sent_again(self):
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active")])
        b, _ = self.course("MATH-140-508", [("Example, Carol", CAROL, "active")])
        self.link(507, a)
        self.link(508, b)
        body = {"sections": [self.sections[507], self.sections[508]], "subject": "S", "body": "B"}

        def send(request_id):
            found = self.ask(f"{BASE}/preview", body).json
            return self.settled(self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": request_id, "confirmation": "send"}))

        self.fake.message_answer = (400, [{"attribute": "recipients", "message": "invalid"}])
        failed = send("fail-1")
        self.assertEqual((failed["status"], failed["recipients"]), ("failed", 0))
        self.assertIn("recipients: invalid", failed["courses"][0]["detail"])
        self.fake.message_answer = (502, {"errors": [{"message": "bad gateway"}]})
        unsure = send("unsure-1")
        self.assertEqual(unsure["status"], "uncertain")
        posts = len(self.fake.sent_messages)
        self.fake.message_answer = None
        self.assertEqual(self.ask(f"{BASE}/send", {**body, "fingerprint": self.ask(f"{BASE}/preview", body).json["fingerprint"],
                                                    "request_id": "unsure-1", "confirmation": "send"}).json["status"], "uncertain")
        self.assertEqual(len(self.fake.sent_messages), posts)  # the same request ID never asks Canvas again
        # Canvas refusing the session mid-send (as Canvas says it no longer knows one): that course is not sent and the next is
        # not tried.
        self.fake.message_answer = (401, {"errors": [{"message": "user authorization required"}], "status": "unauthenticated"})
        stopped = send("signed-out-1")
        self.assertEqual([c["status"] for c in stopped["courses"]], ["failed", "failed"])
        self.assertIn("sign in again", stopped["courses"][1]["detail"])
        self.assertEqual(len(self.fake.sent_messages), posts + 1)
        # Canvas refusing the send to a session it knows (a concluded course): Canvas's words for that course, and the next course
        # is still tried (2026-10-04 review: every 401 was taken for a signed-out session).
        self.fake.message_answer = (401, {"errors": [{"message": "user not authorized to perform that action"}], "status": "unauthorized"})
        refused = send("refused-1")
        self.assertEqual([c["status"] for c in refused["courses"]], ["failed", "failed"])
        self.assertTrue(all("not authorized" in c["detail"] for c in refused["courses"]), refused["courses"])
        self.assertEqual(len(self.fake.sent_messages), posts + 3)
        # Signed out before anything was recorded: a 401 for the page to offer Sign in to Canvas, and no record.
        self.fake.message_answer = None
        found = self.ask(f"{BASE}/preview", body).json
        messages = len(self.rows("canvas_messages"))
        self.fake.sessions.clear()
        answer = self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": "late-1", "confirmation": "send"})
        self.assertEqual(answer.status_code, 401)
        self.assertEqual(len(self.rows("canvas_messages")), messages)
        # An interrupted delivery (no outcome) is uncertain.
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO canvas_messages VALUES ('m-x','req-x',?, 'S','B','[]','workspace','2026-10-01T00:00:00+00:00')", ("a" * 64,))
            db.execute("""INSERT INTO canvas_message_deliveries(id,message_id,course_id,course_name,sections_json,recipients_json,mode,created_at)
                          VALUES ('d-x','m-x',?,'MATH-140-507','[]','[]','sync','2026-10-01T00:00:00+00:00')""", (a,))
            db.commit()
        self.keep()
        interrupted = next(m for m in self.client.get(BASE).json["sent"] if m["id"] == "m-x")
        self.assertEqual(interrupted["status"], "uncertain")
        self.assertIn("interrupted", interrupted["courses"][0]["detail"])

    def test_a_send_runs_in_the_background_and_a_stop_waits_for_it(self):
        """requirement, 2026-10-01: a restart of live 47 s into their first send cut it off. The send now answers at
        once and goes on in the background, and a stop (SIGTERM) lets it finish and refuses new sends before the process ends."""
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active")])
        b, _ = self.course("MATH-140-508", [("Example, Carol", CAROL, "active")])
        self.link(507, a)
        self.link(508, b)
        service = self.app.extensions["canvas_messages"]
        body = {"sections": [self.sections[507], self.sections[508]], "subject": "S", "body": "B"}
        found = self.ask(f"{BASE}/preview", body).json
        self.fake.hold_messages = threading.Event()  # Canvas is slow to answer
        self.addCleanup(self.fake.hold_messages.set)
        answer = self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": "bg-1", "confirmation": "send"})
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual((answer.json["status"], [c["status"] for c in answer.json["courses"]]), ("sending", ["sending", "sending"]))
        self.assertEqual([c["detail"] for c in answer.json["courses"]], ["", ""])
        self.assertEqual(service.busy(), [answer.json["id"]])
        self.assertEqual(self.client.get(BASE).json["sent"][0]["status"], "sending")
        stopped = []
        previous = Mock()  # the handler app.py found installed: rosters._terminate, which closes the catalog's idle connections
        with patch("canvas.stopping.os.kill", side_effect=lambda pid, signum: stopped.append(signum)), \
             patch("canvas.stopping.signal.signal") as installed:
            handler = stopping.stop_handler(self.service.writes, timeout=20, previous=previous)
            began = time.monotonic()
            handler(signal.SIGTERM, None)  # run where Python runs it, in waitress's own loop: it returns at once
            self.assertLess(time.monotonic() - began, 1)
            installed.assert_called_once_with(signal.SIGTERM, previous)  # the signal sent once the send is done goes to it
            time.sleep(0.3)
            self.assertEqual(stopped, [])  # the stop waits for the send
            refused = self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": "bg-2", "confirmation": "send"})
            self.assertEqual(refused.status_code, 503)
            self.assertIn("restarting", refused.json["error"])
            again = self.ask(f"{BASE}/send", {**body, "fingerprint": found["fingerprint"], "request_id": "bg-1", "confirmation": "send"})
            self.assertEqual(again.json["status"], "sending")  # the same request ID still answers with its record
            self.fake.hold_messages.set()
            until = time.monotonic() + 10
            while not stopped and time.monotonic() < until:
                time.sleep(0.05)
        self.assertEqual(stopped, [signal.SIGTERM])
        record = self.client.get(f"{BASE}/{answer.json['id']}").json
        self.assertEqual((record["status"], [c["status"] for c in record["courses"]]), ("accepted", ["accepted", "accepted"]))
        self.assertEqual(len(self.fake.sent_messages), 2)
        self.assertEqual(self.client.get(f"{BASE}/nope").status_code, 404)

    def test_what_a_message_must_hold(self):
        a, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active")])
        self.link(507, a)
        good = {"sections": [self.sections[507]], "subject": "S", "body": "B"}
        for change, status in (({"sections": []}, 400), ({"sections": ["nope"]}, 400), ({"subject": " "}, 400), ({"subject": "x" * 256}, 400),
                               ({"body": "\n"}, 400), ({"body": "x" * 20001}, 400)):
            with self.subTest(change=change):
                self.assertEqual(self.ask(f"{BASE}/preview", {**good, **change}).status_code, status)
        found = self.ask(f"{BASE}/preview", good).json
        # A section without a Canvas course is named with its students, never guessed.
        wider = self.ask(f"{BASE}/preview", {**good, "sections": [self.sections[507], self.sections[508]]}).json
        self.assertEqual((wider["unlinked"], wider["recipients"]), ([{"section": 508, "students": 1}], 1))
        self.assertEqual(self.ask(f"{BASE}/send", {**good, "fingerprint": found["fingerprint"], "request_id": "bad id!", "confirmation": "send"}).status_code, 400)
        self.assertEqual(policy.base_rule("canvas.message_preview").tier, "read")
        rule = policy.base_rule("canvas.message_send")
        self.assertEqual((rule.tier, rule.preview, rule.confirmation), ("approve", "canvas.message_preview", "send"))
