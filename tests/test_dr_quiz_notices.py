"""SCHEDULING "DR QUIZ NOTICES" (scheduling/dr_quiz_notices.py, requirement, 2026-10-15, answers 1(a), 2(a), 3(a), 4(a), 6): the
instructor's "Prescheduled in AIM" mark on a DR quiz row, and the automatic email it starts to each DR quiz taker of the section
(ACCOMMODATIONS "DR QUIZ TAKERS": the flag decides, never a DR booking or Extra Time alone; one email per student, receipts through
the Mail page's own send, never resent automatically, an explicit Retry). Gmail is the fake in
tests/test_gmail_mail.py; nothing reaches AIM or the network."""
import datetime as dt
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from unittest import mock

import test_dr_quizzes as dr_quiz_tests
from test_gmail_mail import ACCOUNT, FakeGmail, FakeTokens
from test_rosters import COLUMNS, STUDENTS, csv_bytes
from test_scheduling_class_schedule import ClassScheduleBase
from test_storage import HEADERS

from assistant import policy
from response_cards.gmail import MAIL_SCOPES, SCOPES

ALICE, BOB, CAROL, DANA = "001234567", "123456789", "222222222", "333333333"
ROSTER = [*STUDENTS,
          ["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "N", "", "", "", ""],  # no address on the roster
          ["", "Example, Dana", DANA, "Registered", "MATH", "U1", "", "N", "", "", "", "dana@example.test"]]
ADDRESSES = {"Example, Alice": "alice@example.test", "Example, Bob": "bob@example.test", "Example, Dana": "dana@example.test"}
SUBJECT = "MATH 140 quizzes ready to register at the DR center (AIM)"
QUIZ5 = "- Quiz 5: Friday, October 23"
QUIZ6 = "- Quiz 6: Monday, October 26"
QUIZ7 = "- Quiz 7: Wednesday, October 28"
QUIZ5_MOVED = "- Quiz 5: Friday, October 30 (new date)"


class DrQuizNoticesTest(ClassScheduleBase):
    bookings = dr_quiz_tests.DrQuizzesTest.bookings
    extra_time = dr_quiz_tests.DrQuizzesTest.extra_time
    imported = dr_quiz_tests.DrQuizzesTest.imported
    item = dr_quiz_tests.DrQuizzesTest.item
    enrollment = dr_quiz_tests.DrQuizzesTest.enrollment
    takers = dr_quiz_tests.DrQuizzesTest.takers

    def setUp(self):
        mail_dir = tempfile.TemporaryDirectory()
        self.addCleanup(mail_dir.cleanup)
        client_file, self.token_file = os.path.join(mail_dir.name, "client.json"), os.path.join(mail_dir.name, "token.json")
        with open(client_file, "w") as handle:
            json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret", "project_id": "faculty-mail-1"}}, handle)
        self.write_token()
        with mock.patch.dict(os.environ, {"FACULTY_GMAIL_CLIENT_FILE": client_file, "FACULTY_GMAIL_TOKEN_FILE": self.token_file}), \
             mock.patch("test_absences.csv_bytes", side_effect=[csv_bytes(ROSTER, COLUMNS), csv_bytes([STUDENTS[1]], COLUMNS)]):  # 507, 508
            super().setUp()
        self.inbox = self.app.extensions["inbox"]
        self.mail = self.app.extensions["gmail_mail"]
        self.google = FakeGmail()
        self.mail.client.http = self.google
        self.mail.google.http = FakeTokens()
        self.notices = self.app.extensions["dr_quiz_notices"]
        self.use()
        # 507's DR quiz takers: Alice (who also has a DR booking), Carol (no address) and Dana. Bob, with a granted Extra Time in 507
        # but no flag, tests in class; 508 has no DR quiz taker.
        self.bookings(dr_quiz_tests.AIM)
        self.extra_time((BOB, "Bob", "507", "Yes", "2026-09-13"))
        self.takers(ALICE, CAROL, DANA)
        self.changed(set=[
            {"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23", "time": "8:00 AM", "place": "HELD 107"},
            {"kind": "quiz", "title": "Quiz 6", "date": "2026-10-26", "time": "9:00 AM–9:20 AM", "place": "ILCB 111"},
            {"kind": "quiz", "title": "Quiz 7", "date": "2026-10-28", "time": "8:00 AM", "place": "HELD 107"},
            {"kind": "quiz", "title": "Quiz 9"}])  # no date yet: not ready

    def write_token(self):
        with open(self.token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": ACCOUNT, "scopes": sorted(set(SCOPES) | set(MAIL_SCOPES)),
                       "connected_at": "2026-09-16T00:00:00+00:00"}, handle)

    # Reads and writes through the routes the spec names.
    def quizzes(self):
        response = self.client.get(f"/api/scheduling/terms/{self.term}/dr-quizzes")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def key(self, title):
        return f"{self.item(title)}:{self.s507}"

    def status(self):
        response = self.client.get(f"/api/scheduling/terms/{self.term}/aim-prescheduling")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def entry(self, title, view=None):
        key = self.key(title)
        found = [q for q in (view or self.status())["quizzes"] if q["key"] == key]
        self.assertEqual(len(found), 1, (title, view))
        return found[0]

    def students(self, title, view=None):
        return {s["name"]: s for s in self.entry(title, view)["students"]}

    def post(self, action, body, headers=HEADERS):
        return self.client.post(f"/api/scheduling/terms/{self.term}/aim-prescheduling/{action}", headers=headers, json=body)

    def mark(self, title, request=None, fingerprint=None, key=None):
        return self.post("mark", {"request_id": request or str(uuid.uuid4()), "key": key or self.key(title),
                                  "fingerprint": fingerprint or self.quizzes()["fingerprint"]})

    def marked(self, *titles):
        for title in titles:
            response = self.mark(title)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def unmark(self, title, request=None):
        return self.post("unmark", {"request_id": request or str(uuid.uuid4()), "key": self.key(title)})

    def retry(self, batch, request=None):
        return self.post("retry", {"request_id": request or str(uuid.uuid4()), "batch": batch})

    def turns(self, limit=10):
        """Worker turns until one has nothing to send; the number that sent."""
        self.notices.wake()
        sent = 0
        while sent < limit and self.notices.process_one():
            sent += 1
        return sent

    def mails(self, start=0):
        return [self.google.parsed(i) for i in range(start, len(self.google.sent))]

    def lines(self, mail):
        return [line for line in mail.get_content().splitlines() if line.startswith("- ")]

    def sends(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM gmail_mail_sends ORDER BY created_at, id")]

    def table(self, name):
        with self.db() as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]

    def test_mark_and_unmark(self):
        before = self.client.get(f"/api/scheduling/terms/{self.term}/dr-quizzes").get_data()
        view = self.status()
        self.assertEqual(view["term_id"], self.term)
        self.assertEqual([q["key"] for q in view["quizzes"]], [q["key"] for q in self.quizzes()["quizzes"]])  # every row, in its order
        self.assertEqual({q["state"] for q in view["quizzes"]}, {"unmarked"})
        self.assertIsNone(self.entry("Quiz 5", view)["mark"])
        self.assertEqual(set(self.students("Quiz 5", view)), {"Example, Alice", "Example, Carol", "Example, Dana"})
        # The key, its readiness and the fingerprint of what was read are checked; nothing is recorded for a refusal.
        self.assertEqual(self.mark("Quiz 5", key=f"no-such-item:{self.s507}").status_code, 400)
        self.assertEqual(self.mark("Quiz 5", key=f"{self.item('Quiz 5')}:{self.s508}").status_code, 400)  # 508 has no DR student
        self.assertEqual(self.mark("Quiz 9").status_code, 400)  # no date yet
        self.assertEqual(self.mark("Quiz 5", fingerprint="0" * 64).status_code, 409)
        self.assertEqual(self.post("mark", {"key": self.key("Quiz 5"), "fingerprint": self.quizzes()["fingerprint"]}).status_code, 400)
        self.assertEqual(self.table("dr_quiz_marks"), [])
        # A mark records the row's date and start; the status read shows it.
        first = self.mark("Quiz 5", request="mark-1")
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        quiz5 = self.entry("Quiz 5", first.json)
        self.assertEqual(quiz5["state"], "marked")
        self.assertEqual((quiz5["mark"]["date"], quiz5["mark"]["start"]), ("2026-10-23", "08:00"))
        self.assertEqual(set(quiz5["mark"]), {"date", "start", "actor", "at"})
        self.assertEqual(self.entry("Quiz 6", first.json)["state"], "unmarked")
        self.assertEqual(self.students("Quiz 5", first.json)["Example, Alice"]["status"], "waiting")
        self.assertEqual(self.status(), first.json)
        # Request IDs as in `link`: an exact repeat answers the state; the same ID with another body is a conflict.
        again = self.mark("Quiz 5", request="mark-1")
        self.assertEqual((again.status_code, again.json), (200, first.json))
        self.assertEqual(self.mark("Quiz 6", request="mark-1").status_code, 409)
        # A key already marked records nothing.
        self.assertEqual(self.mark("Quiz 5").status_code, 200)
        marks = self.table("dr_quiz_marks")
        self.assertEqual([(m["action"], m["date"], m["start"], m["request_id"], m["section_id"], m["item_id"]) for m in marks],
                         [("mark", "2026-10-23", "08:00", "mark-1", self.s507, self.item("Quiz 5"))])
        # Unmark before any notice went out returns the key to unmarked.
        undone = self.unmark("Quiz 5", request="unmark-1")
        self.assertEqual(undone.status_code, 200, undone.get_data(as_text=True))
        self.assertEqual(self.entry("Quiz 5", undone.json)["state"], "unmarked")
        self.assertIsNone(self.entry("Quiz 5", undone.json)["mark"])
        self.assertEqual(self.unmark("Quiz 5", request="unmark-1").json, undone.json)
        self.assertEqual([(m["action"], m["date"], m["start"]) for m in self.table("dr_quiz_marks")],
                         [("mark", "2026-10-23", "08:00"), ("unmark", "", "")])
        # The marks are append-only, the Job 1 read is byte-identical, and no mail went.
        with self.db() as db:
            for sql in ("UPDATE dr_quiz_marks SET actor='x'", "DELETE FROM dr_quiz_marks"):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "SCHEDULING"):
                    db.execute(sql)
        self.assertEqual(self.client.get(f"/api/scheduling/terms/{self.term}/dr-quizzes").get_data(), before)
        self.assertEqual((self.google.sent, self.sends()), ([], []))
        # No UIN or enrollment ID in the status read; an unknown term is 404.
        body = self.client.get(f"/api/scheduling/terms/{self.term}/aim-prescheduling").get_data(as_text=True)
        with self.db() as db:
            enrollments = [r[0] for r in db.execute("SELECT id FROM student_enrollments")]
        for private in (ALICE, BOB, CAROL, DANA, *enrollments):
            self.assertNotIn(private, body)
        self.assertEqual(self.client.get("/api/scheduling/terms/no-such-term/aim-prescheduling").status_code, 404)
        self.assertEqual(self.client.post("/api/scheduling/terms/no-such-term/aim-prescheduling/mark", headers=HEADERS,
                                          json={"request_id": "x", "key": self.key("Quiz 5"), "fingerprint": "0" * 64}).status_code, 404)

    def test_no_email_without_a_mark(self):
        for _ in range(3):
            self.assertEqual(self.turns(), 0)
        self.assertEqual((self.google.sent, self.sends(), self.table("dr_quiz_notices")), ([], [], []))
        # Gmail not connected: a marked quiz waits, and a turn sends nothing.
        os.remove(self.token_file)
        self.marked("Quiz 5")
        self.notices.wake()
        self.assertFalse(self.notices.process_one())
        self.assertEqual((self.google.sent, self.sends(), self.table("dr_quiz_notices")), ([], [], []))
        self.assertEqual(self.students("Quiz 5")["Example, Alice"]["status"], "waiting")

    def test_mark_sends_one_email_per_student(self):
        view = self.marked("Quiz 5", "Quiz 6")
        people = self.students("Quiz 5", view)
        self.assertEqual({name: s["status"] for name, s in people.items()},
                         {"Example, Alice": "waiting", "Example, Dana": "waiting", "Example, Carol": "skipped"})
        self.assertEqual(people["Example, Carol"]["detail"], "no email address on the roster")
        self.assertEqual(self.turns(), 2)  # one turn per student with an address
        mails = self.mails()
        self.assertEqual(sorted(m["To"] for m in mails), ["alice@example.test", "dana@example.test"])
        for mail in mails:
            self.assertEqual((mail["Cc"], mail["Bcc"], mail["Subject"]), (None, None, SUBJECT))
            self.assertEqual((mail["From"].addresses[0].addr_spec, mail["From"].addresses[0].display_name), (ACCOUNT, "Christopher Sinkule"))
            text = mail.get_content().strip()
            self.assertTrue(text.startswith("Hello,"), text)
            self.assertIn("MATH 140 section 507", text)
            self.assertIn("AIM", text)
            self.assertIn("Please register for each one in AIM", text)
            self.assertIn("The DR center sets the time and room.", text)
            for clock_or_room in ("8:00", "9:00", "AM", "HELD 107", "ILCB 111"):  # the DR center sets these (requirement, 2026-10-18)
                self.assertNotIn(clock_or_room, text)
            self.assertTrue(text.endswith("Professor Sinkule"), text)
            self.assertEqual(self.lines(mail), [QUIZ5, QUIZ6])
            for other in ("Example", "Alice", "Dana", "Carol", "Bob"):
                self.assertNotIn(other, text)
        sends = self.sends()
        self.assertEqual([s["actor"] for s in sends], ["dr-quiz-notice", "dr-quiz-notice"])
        self.assertTrue(all(s["request_id"].startswith("dr-quiz-notice:") for s in sends))
        self.assertEqual({s["request_id"] for s in sends}, {n["request_id"] for n in self.table("dr_quiz_notices")})
        self.assertEqual(len(self.table("dr_quiz_notices")), 4)  # two students, two quizzes, attempt 1
        self.assertEqual({n["attempt"] for n in self.table("dr_quiz_notices")}, {1})
        for title in ("Quiz 5", "Quiz 6"):
            people = self.students(title)
            self.assertEqual((people["Example, Alice"]["status"], people["Example, Dana"]["status"]), ("accepted", "accepted"))
            self.assertTrue(people["Example, Alice"]["batch"].startswith("dr-quiz-notice:"))
            self.assertEqual((people["Example, Carol"]["status"], people["Example, Carol"]["detail"]), ("skipped", "no email address on the roster"))
        self.assertEqual(self.mails()[0]["Subject"], SUBJECT)  # listed on Mail › Sent as well
        self.assertIn(SUBJECT, [s["subject"] for s in self.client.get("/api/mail/sent").json["sent"]])
        # Never twice.
        for _ in range(3):
            self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 2)
        # A third quiz marked: one new email per student, listing only it.
        self.marked("Quiz 7")
        self.assertEqual(self.turns(), 2)
        third = self.mails(2)
        self.assertEqual(sorted(m["To"] for m in third), ["alice@example.test", "dana@example.test"])
        self.assertEqual([self.lines(m) for m in third], [[QUIZ7], [QUIZ7]])
        self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 4)

    def test_uncertain_and_failed_wait_for_retry(self):
        self.marked("Quiz 5", "Quiz 6")
        self.notices.wake()
        self.google.lose_answer = True  # the answer never arrives: uncertain
        self.notices.process_one()
        self.google.fail[("POST", "/messages/send")] = (400, {"error": {"code": 400, "message": "Invalid To header"}})  # refused: failed
        self.notices.process_one()
        self.assertEqual(len(self.sends()), 2)
        people = self.students("Quiz 5")
        outcomes = {people[name]["status"]: name for name in ("Example, Alice", "Example, Dana")}
        self.assertEqual(set(outcomes), {"uncertain", "failed"})
        self.assertEqual({p["status"] for p in self.students("Quiz 6").values()}, {"uncertain", "failed", "skipped"})
        # Nothing is sent again automatically, whatever the status.
        for _ in range(3):
            self.assertEqual(self.turns(), 0)
        self.assertEqual((len(self.sends()), len(self.google.sent)), (2, 1))
        self.assertEqual(len(self.table("dr_quiz_notices")), 4)
        # An explicit Retry of the failed batch sends one new email under a new gmail request ID.
        failed = people[outcomes["failed"]]["batch"]
        retried = self.retry(failed, request="retry-1")
        self.assertEqual(retried.status_code, 200, retried.get_data(as_text=True))
        batch = "dr-quiz-retry:" + hashlib.sha256((self.term + "retry-1").encode()).hexdigest()[:32]
        self.assertEqual(len(self.google.sent), 2)
        mail = self.google.parsed()
        self.assertEqual((mail["To"], mail["Subject"], self.lines(mail)), (ADDRESSES[outcomes["failed"]], SUBJECT, [QUIZ5, QUIZ6]))
        self.assertEqual(self.sends()[-1]["request_id"], batch)
        for title in ("Quiz 5", "Quiz 6"):
            student = self.students(title, retried.json)[outcomes["failed"]]
            self.assertEqual((student["status"], student["batch"]), ("accepted", batch))
        retry_rows = [n for n in self.table("dr_quiz_notices") if n["request_id"] == batch]
        self.assertEqual(sorted(n["attempt"] for n in retry_rows), [2, 2])
        # The same retry request again sends nothing; an accepted batch, or one already retried, is not retried.
        self.assertEqual(self.retry(failed, request="retry-1").status_code, 200)
        self.assertEqual(len(self.google.sent), 2)
        self.assertEqual(self.retry(batch).status_code, 409)
        self.assertEqual(self.retry(failed).status_code, 409)
        # The uncertain batch waits for its own Retry too.
        uncertain = people[outcomes["uncertain"]]["batch"]
        self.assertEqual(self.retry(uncertain, request="retry-2").status_code, 200)
        self.assertEqual((len(self.google.sent), self.google.parsed()["To"]), (3, ADDRESSES[outcomes["uncertain"]]))
        self.assertEqual(self.students("Quiz 5")[outcomes["uncertain"]]["status"], "accepted")
        self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 3)

    def test_changed_quiz_lapses_and_remark_sends_update(self):
        self.marked("Quiz 5")
        self.assertEqual(self.turns(), 2)
        self.assertEqual([self.lines(m) for m in self.mails()], [[QUIZ5], [QUIZ5]])
        # The quiz moves: the mark lapses and nothing goes.
        quiz5 = next(i for i in self.plan()["items"] if i["title"] == "Quiz 5")
        self.changed(set=[{**quiz5, "date": "2026-10-30"}])
        entry = self.entry("Quiz 5")
        self.assertEqual((entry["state"], entry["mark"]["date"], entry["mark"]["start"]), ("changed", "2026-10-23", "08:00"))
        self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 2)
        # A mark whose notice went out is not undone.
        self.assertEqual(self.unmark("Quiz 5").status_code, 409)
        # Marked again at the new date: one updated notice per student.
        remarked = self.marked("Quiz 5")
        entry = self.entry("Quiz 5", remarked)
        self.assertEqual((entry["state"], entry["mark"]["date"]), ("marked", "2026-10-30"))
        self.assertEqual(self.turns(), 2)
        update = self.mails(2)
        self.assertEqual(sorted(m["To"] for m in update), ["alice@example.test", "dana@example.test"])
        self.assertEqual([self.lines(m) for m in update], [[QUIZ5_MOVED], [QUIZ5_MOVED]])
        self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 4)
        # Only its time changes, on the same day: the mark lapses, and marking it again tells nobody anything new.
        quiz5 = next(i for i in self.plan()["items"] if i["title"] == "Quiz 5")
        self.changed(set=[{**quiz5, "time": "10:00 AM"}])
        self.assertEqual(self.entry("Quiz 5")["state"], "changed")
        again = self.marked("Quiz 5")
        self.assertEqual({s["status"] for s in self.students("Quiz 5", again).values()} - {"skipped"}, {"accepted"})
        self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 4)

    def test_sweep_reaches_a_later_dr_student(self):
        self.marked("Quiz 5", "Quiz 6")
        self.assertEqual(self.turns(), 2)
        self.assertNotIn("Example, Bob", self.students("Quiz 6"))  # his Extra Time alone: no email
        with mock.patch("scheduling.clock.campus_today", return_value=dt.date(2026, 10, 24)):  # Quiz 5 is past
            # Bob's flag is ticked after the mark, and Dana's unticked after her notice: that sends her nothing.
            self.takers(BOB)
            self.takers(DANA, on=False)
            self.assertEqual(self.students("Quiz 6")["Example, Bob"]["status"], "waiting")
            self.assertNotIn("Example, Dana", self.students("Quiz 6"))
            self.assertEqual(self.turns(), 1)
            mail = self.google.parsed()
            self.assertEqual((mail["To"], mail["Subject"], self.lines(mail)), ("bob@example.test", SUBJECT, [QUIZ6]))
            self.assertEqual(self.students("Quiz 6")["Example, Bob"]["status"], "accepted")
            self.assertEqual(self.turns(), 0)
            # A quiz marked now reaches the flagged students only.
            self.marked("Quiz 7")
            self.assertEqual(self.turns(), 2)
            self.assertEqual(sorted(m["To"] for m in self.mails(3)), ["alice@example.test", "bob@example.test"])
            self.assertEqual(self.turns(), 0)
        self.assertEqual(len(self.google.sent), 5)

    def test_routes_policy_and_wiring(self):
        base = "/api/scheduling/terms/<term_id>/aim-prescheduling"
        routes = {rule.endpoint: (rule.rule, sorted(rule.methods - {"HEAD", "OPTIONS"})) for rule in self.app.url_map.iter_rules()
                  if "aim_prescheduling" in rule.endpoint or "aim-prescheduling" in rule.rule}
        self.assertEqual(routes, {"scheduling.aim_prescheduling": (base, ["GET"]),
                                  "scheduling.aim_prescheduling_mark": (base + "/mark", ["POST"]),
                                  "scheduling.aim_prescheduling_unmark": (base + "/unmark", ["POST"]),
                                  "scheduling.aim_prescheduling_retry": (base + "/retry", ["POST"])})
        fingerprint = self.quizzes()["fingerprint"]
        for action, body in (("mark", {"request_id": "r1", "key": self.key("Quiz 5"), "fingerprint": fingerprint}),
                             ("unmark", {"request_id": "r2", "key": self.key("Quiz 5")}),
                             ("retry", {"request_id": "r3", "batch": "dr-quiz-notice:" + "0" * 32})):
            refused = self.post(action, body, headers={})
            self.assertEqual(refused.status_code, 403, action)
        self.assertEqual(self.table("dr_quiz_marks"), [])
        for endpoint in ("scheduling.aim_prescheduling_mark", "scheduling.aim_prescheduling_unmark", "scheduling.aim_prescheduling_retry"):
            self.assertIn(endpoint, policy.REQUEST_IDS)
            self.assertEqual(policy.base_rule(endpoint, {"term_id": self.term}).tier, "approve")
        self.assertNotIn("scheduling.aim_prescheduling", policy.GET_BLOCKED)
        # The worker's stage is the app's instance, and work_once gives it a turn.
        self.assertIs(self.inbox.dr_quiz_notices, self.app.extensions["dr_quiz_notices"])
        with mock.patch.object(self.notices, "process_one", return_value=True) as turn:
            for _ in range(60):
                try:
                    self.inbox.work_once()
                except Exception:  # noqa: BLE001 - another stage's failure in this scratch workspace is not this check
                    pass
                if turn.called:
                    break
        self.assertTrue(turn.called)
        # Job 1's read keeps its shape, with DR DATE's class_date and dr_date (2026-10-31).
        self.assertEqual(set(self.quizzes()), {"term_id", "code", "term_label", "today", "sections", "quizzes", "fingerprint"})
        self.assertEqual(set(self.quizzes()["quizzes"][0]), {"key", "item", "title", "assignment_id", "section_id", "section", "date", "class_date", "dr_date", "time",
                                                             "start", "end", "place", "time_source", "minutes", "ready", "reason", "aim"})
