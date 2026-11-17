"""SCHEDULING "CLASS SCHEDULE: DR QUIZZES" (scheduling/dr_quizzes.py, requirement, 2026-10-15, answers 3(a), 4(a), 5(a)): the term's
upcoming class-schedule quizzes to preschedule with the DR center, for sections with a DR quiz taker (ACCOMMODATIONS "DR QUIZ
TAKERS": a student whose flag is on, never one only because of a DR booking or Extra Time), compared by date with the imported DR
bookings. A read only: it writes nothing anywhere."""
import hashlib
import io
import json
import uuid
from datetime import datetime
from unittest import mock

import test_accommodations as accommodation_tests
import test_dr_exams as dr_tests
from test_rosters import COLUMNS, STUDENTS, csv_bytes
from test_scheduling_class_schedule import ClassScheduleBase, db_event
from test_storage import HEADERS

from assistant import policy
from rosters import connect

AIM = {"Section": "507", "Exam Type": "Quiz #5", "Date": datetime(2026, 10, 23), "Start Time": "06:30 PM", "End Time": "06:50 PM", "Length": 30}
ALICE, BOB, CAROL = "001234567", "123456789", "222222222"
# One section each, as on the registrar's rosters: Alice and Carol in 507, Bob in 508.
ROSTERS = ([STUDENTS[0], ["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "N", "", "", "", "carol@example.test"]], [STUDENTS[1]])


class DrQuizzesTest(ClassScheduleBase):
    def setUp(self):
        with mock.patch("test_absences.csv_bytes", side_effect=[csv_bytes(rows, COLUMNS) for rows in ROSTERS]):
            super().setUp()
        self.inbox = self.app.extensions["inbox"]

    def enrollment(self, uin):
        with self.db() as db:
            return db.execute("SELECT id FROM student_enrollments WHERE term_id=? AND uin=?", (self.term, uin)).fetchone()[0]

    def takers(self, *uins, on=True):
        """Ticks (or unticks) each student's DR quiz taker flag through its own route (ACCOMMODATIONS "DR QUIZ TAKERS")."""
        for uin in uins:
            saved = self.client.post(f"/api/accommodations/dr-quiz-takers/{self.enrollment(uin)}", headers=HEADERS,
                                     json={"on": on, "request_id": str(uuid.uuid4())})
            self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))

    # Imports go through the app's own DR and accommodation importers, exactly as the instructor's exports do.
    def bookings(self, *rows):
        content = dr_tests.workbook([dr_tests.booking(**row) for row in rows])
        self.imported("/api/accommodations/exams/", content)

    def extra_time(self, *rows):
        """rows: (uin, first name, section, extra time value, last updated)."""
        content = accommodation_tests.workbook([["Example", first, uin, "", "MATH", "140", section, "Read", updated, value, "Yes", ""]
                                                for uin, first, section, value, updated in rows])
        self.imported("/api/accommodations/", content)

    def imported(self, prefix, content):
        files = lambda: {"file": (io.BytesIO(content), "export.xlsx"), "term": json.dumps(None)}
        preview = self.client.post(prefix + "preview", data=files(), headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        job = self.client.post(prefix + "import", headers=HEADERS, data={**files(), "preview_hash": preview.json["preview_hash"],
                                                                          "idempotency_key": str(uuid.uuid4())})
        self.assertEqual(job.status_code, 202, job.json)
        self.assertTrue(self.inbox.process_one())
        self.assertEqual(self.inbox.get(job.json["id"])["status"], "completed", self.inbox.get(job.json["id"]))

    def read(self, term=None):
        response = self.client.get(f"/api/scheduling/terms/{term or self.term}/dr-quizzes")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def rows(self, view=None, title=None):
        return [q for q in (view or self.read())["quizzes"] if title in (None, q["title"])]

    def row(self, title, section, view=None):
        found = [q for q in self.rows(view, title) if q["section"] == section]
        self.assertEqual(len(found), 1, (title, section, self.rows(view)))
        return found[0]

    def item(self, title):
        return next(i["item"] for i in self.plan()["items"] if i["title"] == title)

    def test_sections_with_dr_quiz_takers_only(self):
        self.use()
        self.changed(set=[{"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23"}])
        empty = self.read()
        self.assertEqual((empty["term_id"], empty["today"], empty["sections"], empty["quizzes"]), (self.term, "2026-10-12", [], []))
        # Alice holds two DR bookings in 507 and Bob a granted Extra Time in 508: neither alone makes a DR quiz taker.
        self.bookings(AIM, {**AIM, "Exam Type": "Test 1", "Date": datetime(2026, 9, 17)})
        self.extra_time((ALICE, "Alice", "507", "Yes", "2026-09-13"), (BOB, "Bob", "508", "Yes", "2026-09-13"))
        self.assertEqual((self.read()["sections"], self.read()["quizzes"]), ([], []))
        # Alice's flag lists 507 only.
        self.takers(ALICE)
        view = self.read()
        self.assertEqual(view["sections"], [{"section_id": self.s507, "section": 507, "dr_students": 1}])
        self.assertEqual([(q["title"], q["section"]) for q in view["quizzes"]], [("Quiz 5", 507)])  # 508's quiz is not listed
        # Bob's flag lists 508 as well.
        self.takers(BOB)
        view = self.read()
        self.assertEqual(view["sections"], [{"section_id": self.s507, "section": 507, "dr_students": 1},
                                            {"section_id": self.s508, "section": 508, "dr_students": 1}])
        self.assertEqual([(q["title"], q["section"], q["key"]) for q in view["quizzes"]],
                         [("Quiz 5", 507, f"{self.item('Quiz 5')}:{self.s507}"), ("Quiz 5", 508, f"{self.item('Quiz 5')}:{self.s508}")])
        # Carol, flagged with no booking or accommodation record: two distinct students in 507.
        self.takers(CAROL)
        self.assertEqual([s["dr_students"] for s in self.read()["sections"]], [2, 1])
        # Unticked, Bob no longer counts and 508 is not listed; a student dropped from 507 is not its DR quiz taker either.
        self.takers(BOB, on=False)
        self.assertEqual([(s["section"], s["dr_students"]) for s in self.read()["sections"]], [(507, 2)])
        dropped = self.client.post(f"/api/course-groups/{self.group}/sections/507/roster/drop", headers=HEADERS,
                                   json={"enrollment_ids": [self.enrollment(CAROL)], "request_id": "drop-carol"})
        self.assertEqual(dropped.status_code, 200, dropped.get_data(as_text=True))
        self.assertEqual([(s["section"], s["dr_students"]) for s in self.read()["sections"]], [(507, 1)])

    def test_upcoming_quiz_rows_only(self):
        self.use()
        self.takers(ALICE)
        self.changed(set=[
            {"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23", "time": "8:00 AM"},
            {"kind": "quiz", "title": "Quiz 4", "date": "2026-10-12", "time": "8:00 AM"},  # campus today: still upcoming
            {"kind": "quiz", "title": "Quiz 3", "date": "2026-09-25", "time": "8:00 AM"},  # given more than PAST_DAYS before today
            {"kind": "quiz", "title": "Quiz 9"},  # no date yet
            {"kind": "quiz", "title": "Quiz 6", "date": "2026-10-26", "sections": [self.s508]},  # not for 507
            {"kind": "quiz", "title": "Quiz 7", "date": "2026-10-28", "sections": [self.s507]},
            {"kind": "exam", "title": "Exam 3", "date": "2026-11-17", "time": "7:30 PM–9:30 PM"},
            {"kind": "final", "title": "Final exam", "date": "2026-12-11"},
            {"kind": "homework", "title": "HW 9", "date": "2026-11-01"},
            {"kind": "other", "title": "Q-drop deadline", "date": "2026-11-13"}])
        view = self.read()
        self.assertEqual([(q["title"], q["section"], q["date"]) for q in view["quizzes"]],
                         [("Quiz 4", 507, "2026-10-12"), ("Quiz 5", 507, "2026-10-23"), ("Quiz 7", 507, "2026-10-28"), ("Quiz 9", 507, "")])
        undated = self.row("Quiz 9", 507, view)
        self.assertEqual((undated["ready"], undated["reason"], undated["start"], undated["assignment_id"]), (False, "No date yet", "", None))
        self.assertEqual((self.row("Quiz 5", 507, view)["ready"], self.row("Quiz 5", 507, view)["reason"]), (True, ""))
        # Linked or not, a row stays listed until its work is certified.
        quiz, _ = self.assessment("quiz-five", "Quiz 5")
        self.assertEqual(self.link(self.item("Quiz 5"), assignment_id=quiz).status_code, 200)
        self.assertEqual(self.row("Quiz 5", 507)["assignment_id"], quiz)
        with mock.patch("certified_archive.certified", return_value={(quiz, self.term): {"plan_id": "p", "certification_id": "c", "certified_at": "x"}}):
            self.assertEqual([q["title"] for q in self.read()["quizzes"]], ["Quiz 4", "Quiz 7", "Quiz 9"])

    def test_times_follow_records_and_class_meetings(self):
        self.use()
        self.takers(ALICE, BOB)
        self.changed(set=[
            {"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23", "time": "8:00 AM", "place": "HELD 107",
             "section_values": {self.s507: {"date": "2026-10-21", "time": "9:00 AM–9:20 AM", "place": "ILCB 111"}}},
            {"kind": "quiz", "title": "Quiz 6", "date": "2026-10-26", "time": "8:00 AM"},
            {"kind": "quiz", "title": "Quiz 7", "date": "2026-10-30"},  # a Friday class day, no time written
            {"kind": "quiz", "title": "Quiz 8", "date": "2026-10-31"}])  # a Saturday: no class meeting
        quiz, event = self.assessment("quiz-6", "Quiz 6", sections=(507, 508))
        self.assertEqual(self.link(self.item("Quiz 6"), assignment_id=quiz).status_code, 200)
        event = self.exams.get(db_event(self, quiz))
        self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Quiz 6", "kind": "main", "date": "2026-10-28",
                                              "time": "8:00 AM", "location": "HELD 100"})
        view = self.read()
        fields = ("date", "time", "start", "end", "place", "time_source", "ready", "reason", "minutes")
        own = self.row("Quiz 5", 507, view)  # the section's own values beat the row's
        self.assertEqual(tuple(own[f] for f in fields), ("2026-10-21", "9:00 AM–9:20 AM", "09:00", "09:20", "ILCB 111", "planned", True, "", 20))
        other = self.row("Quiz 5", 508, view)
        self.assertEqual(tuple(other[f] for f in fields), ("2026-10-23", "8:00 AM", "08:00", "", "HELD 107", "planned", True, "", 20))
        linked = self.row("Quiz 6", 507, view)  # the linked record's dated main sitting
        self.assertEqual((linked["date"], linked["start"], linked["time_source"], linked["assignment_id"], linked["ready"]),
                         ("2026-10-28", "08:00", "exam", quiz, True))
        self.assertEqual([q["date"] for q in self.rows(view, "Quiz 6")], ["2026-10-28", "2026-10-28"])
        mine, theirs = self.row("Quiz 7", 507, view), self.row("Quiz 7", 508, view)  # each section's own class meeting that day
        self.assertEqual(((mine["start"], mine["end"], mine["time_source"], mine["ready"]), (theirs["start"], theirs["end"], theirs["time_source"])),
                         (("08:00", "08:50", "class", True), ("09:10", "10:00", "class")))
        weekend = self.row("Quiz 8", 507, view)
        self.assertEqual((weekend["date"], weekend["start"], weekend["end"], weekend["time_source"], weekend["ready"], weekend["reason"], weekend["minutes"]),
                         ("2026-10-31", "", "", "", False, "No time yet", 20))
        # Sorted by date (undated last), then section number, then title.
        self.assertEqual([(q["date"], q["section"]) for q in view["quizzes"]],
                         [("2026-10-21", 507), ("2026-10-23", 508), ("2026-10-28", 507), ("2026-10-28", 508), ("2026-10-30", 507), ("2026-10-30", 508),
                          ("2026-10-31", 507), ("2026-10-31", 508)])

    def test_aim_status_from_imported_bookings(self):
        self.use()
        # Alice: "Quiz #5" in 507 on Oct 23; "Quiz 6" booked only in 508 (Bob, whose booking lists 508).
        self.bookings(AIM, {**AIM, "UIN": BOB, "First Name": "Bob", "Section": "508", "Exam Type": "Quiz 6", "Date": datetime(2026, 10, 26)})
        self.takers(ALICE, BOB)
        self.changed(set=[{"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23"}, {"kind": "quiz", "title": "Quiz 6", "date": "2026-10-26"},
                          {"kind": "quiz", "title": "Quiz 10"}])
        view = self.read()
        self.assertEqual(self.row("Quiz 5", 507, view)["aim"], {"status": "in_aim", "bookings": 1, "dates": ["2026-10-23"]})
        self.assertEqual(self.row("Quiz 6", 507, view)["aim"], {"status": "not_in_aim", "bookings": 0, "dates": []})  # 508's does not count
        self.assertEqual(self.row("Quiz 6", 508, view)["aim"], {"status": "in_aim", "bookings": 1, "dates": ["2026-10-26"]})
        self.assertEqual(self.row("Quiz 5", 508, view)["aim"]["status"], "not_in_aim")
        self.assertEqual(self.row("Quiz 10", 507, view)["aim"]["status"], "not_in_aim")
        # Moving the planned day: AIM differs, with the booking's date.
        quiz5 = next(i for i in self.plan()["items"] if i["title"] == "Quiz 5")
        self.changed(set=[{**quiz5, "date": "2026-10-30"}])
        self.assertEqual(self.row("Quiz 5", 507)["aim"], {"status": "aim_differs", "bookings": 1, "dates": ["2026-10-23"]})
        # A booked quiz with no planned date yet differs too; only dates are compared (the 6:30 PM start never matters).
        self.changed(set=[{**quiz5, "date": ""}])
        self.assertEqual(self.row("Quiz 5", 507)["aim"], {"status": "aim_differs", "bookings": 1, "dates": ["2026-10-23"]})
        body = self.client.get(f"/api/scheduling/terms/{self.term}/dr-quizzes").get_data(as_text=True)
        with self.db() as db:
            enrollments = [r[0] for r in db.execute("SELECT DISTINCT enrollment_id FROM dr_exam_bookings")]
        for private in ("Alice", "Bob", "Example", ALICE, BOB, "alice@example.test", *enrollments):
            self.assertNotIn(private, body)

    def test_fingerprint_and_read_only_route(self):
        self.use()
        self.bookings(AIM)
        self.takers(ALICE)
        self.changed(set=[{"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23"}, {"kind": "quiz", "title": "Quiz 6"}])

        def counts():
            with self.db() as db:
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                return {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}

        before = counts()
        first, second = self.read(), self.read()
        self.assertEqual(counts(), before)  # plan revisions, DR tables and every other table unchanged
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"term_id", "code", "term_label", "today", "sections", "quizzes", "fingerprint"})
        self.assertEqual(first["code"], "MATH 140")
        self.assertIn("Fall", first["term_label"])
        canonical = json.dumps({"sections": first["sections"], "quizzes": first["quizzes"]}, sort_keys=True, separators=(",", ":"))
        self.assertEqual(first["fingerprint"], hashlib.sha256(canonical.encode()).hexdigest())
        self.assertEqual(set(first["quizzes"][0]), {"key", "item", "title", "assignment_id", "section_id", "section", "date", "class_date", "dr_date",
                                                    "time", "start", "end", "place", "time_source", "minutes", "ready", "reason", "aim"})
        # The module is the read the route answers.
        from scheduling import dr_quizzes
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("BEGIN")
            self.assertEqual(json.loads(json.dumps(dr_quizzes.term_quizzes(db, self.term))), first)
        # A change to a listed quiz changes the fingerprint.
        quiz6 = next(i for i in self.plan()["items"] if i["title"] == "Quiz 6")
        self.changed(set=[{**quiz6, "date": "2026-10-26"}])
        self.assertNotEqual(self.read()["fingerprint"], first["fingerprint"])
        self.assertEqual(self.client.get("/api/scheduling/terms/no-such-term/dr-quizzes").status_code, 404)
        self.assertEqual(self.client.post(f"/api/scheduling/terms/{self.term}/dr-quizzes", headers=HEADERS, json={}).status_code, 405)
        routes = {rule.endpoint: (rule.rule, sorted(rule.methods - {"HEAD", "OPTIONS"})) for rule in self.app.url_map.iter_rules()
                  if ("dr_quiz" in rule.endpoint or "dr-quiz" in rule.rule) and not rule.endpoint.startswith("dr_quiz_takers.")}  # not DR QUIZ TAKERS
        self.assertEqual(routes, {"scheduling.dr_quizzes": ("/api/scheduling/terms/<term_id>/dr-quizzes", ["GET"]),
                                  "scheduling.dr_quiz_date": ("/api/scheduling/terms/<term_id>/dr-quizzes/dr-date", ["POST"])})  # DR DATE
        self.assertNotIn("scheduling.dr_quizzes", policy.GET_BLOCKED)
        self.assertIn("scheduling.dr_quiz_date", policy.REQUEST_IDS)  # the Assistant's card gets a fresh request ID
        self.assertEqual(policy.base_rule("scheduling.dr_quiz_date", {"term_id": self.term}).tier, "approve")

    def test_a_certified_quiz_stays_while_its_dr_date_is_ahead(self):
        """CERTIFIED WITH A DR DATE AHEAD (requirement, 2026-11-01: Quiz 6, certified after class, left the DR quizzes and Send to AIM
        before its Nov 4 DR sitting's accessible copy could be uploaded): a certified quiz stays listed for a section whose DR date is
        today or ahead, and only for it."""
        self.use()
        self.takers(ALICE, BOB)
        self.changed(set=[{"kind": "quiz", "title": "Quiz 3", "date": "2026-10-09"}])
        quiz, _ = self.assessment("quiz-three", "Quiz 3")
        self.assertEqual(self.link(self.item("Quiz 3"), assignment_id=quiz).status_code, 200)
        dated = self.client.post(f"/api/scheduling/terms/{self.term}/dr-quizzes/dr-date", headers=HEADERS,
                                 json={"request_id": str(uuid.uuid4()), "key": self.row("Quiz 3", 507)["key"], "date": "2026-10-14"})
        self.assertEqual(dated.status_code, 200, dated.get_data(as_text=True))
        from scheduling import clock
        with mock.patch("certified_archive.certified", return_value={(quiz, self.term): {"plan_id": "p", "certification_id": "c", "certified_at": "x"}}):
            self.assertEqual([(q["title"], q["section"], q["date"], q["ready"]) for q in self.read()["quizzes"]], [("Quiz 3", 507, "2026-10-14", True)])
            with mock.patch.object(clock, "campus_today", return_value=clock.dt.date(2026, 10, 14)):
                self.assertEqual([q["section"] for q in self.read()["quizzes"]], [507])
            with mock.patch.object(clock, "campus_today", return_value=clock.dt.date(2026, 10, 15)):
                self.assertEqual(self.read()["quizzes"], [])

    def test_dr_date_for_a_quiz_given_in_class(self):
        """DR DATE (requirement, 2026-10-31): a quiz given in class in the last PAST_DAYS days stays listed, not ready, until a DR date is
        set; the DR date is then the row's date, at that section's class meeting, and what Send to AIM would add."""
        self.use()
        self.takers(ALICE, BOB)
        self.changed(set=[{"kind": "quiz", "title": "Quiz 3", "date": "2026-10-09"},  # a Friday, three days before campus today
                          {"kind": "quiz", "title": "Quiz 2", "date": "2026-09-25"}])  # longer ago: not listed
        view = self.read()
        self.assertEqual([(q["title"], q["section"]) for q in view["quizzes"]], [("Quiz 3", 507), ("Quiz 3", 508)])
        given = self.row("Quiz 3", 507, view)
        self.assertEqual(tuple(given[f] for f in ("date", "class_date", "dr_date", "ready", "reason")), ("2026-10-09", "2026-10-09", "", False, "Given in class"))
        self.assertEqual(self.client.get(f"/api/scheduling/terms/{self.term}/aim-send").json["quizzes"], [])  # nothing to send yet

        def dated(key, date, request_id=None, status=200):
            response = self.client.post(f"/api/scheduling/terms/{self.term}/dr-quizzes/dr-date", headers=HEADERS,
                                        json={"request_id": request_id or str(uuid.uuid4()), "key": key, "date": date})
            self.assertEqual(response.status_code, status, response.get_data(as_text=True))
            return response.json

        def stored():
            with self.db() as db:
                return [tuple(r) for r in db.execute("SELECT section_id, date FROM dr_quiz_sittings ORDER BY id")]

        answer = dated(given["key"], "2026-10-14", "dr-date-1")  # a Wednesday
        self.assertEqual(answer, self.read())  # the route answers the read
        sitting = self.row("Quiz 3", 507, answer)
        self.assertEqual(tuple(sitting[f] for f in ("date", "class_date", "dr_date", "start", "end", "time_source", "ready", "reason")),
                         ("2026-10-14", "2026-10-09", "2026-10-14", "08:00", "08:50", "class", True, ""))
        self.assertEqual(self.row("Quiz 3", 508, answer)["dr_date"], "")  # the other section's row is its own
        self.assertNotEqual(answer["fingerprint"], view["fingerprint"])
        plan = self.client.get(f"/api/scheduling/terms/{self.term}/aim-send").json["quizzes"]
        self.assertEqual([(q["section"], q["date"], q["aim_time"]) for q in plan], [(507, "2026-10-14", "09:00")])  # 10 minutes after class
        # Exact request IDs; the same date again records nothing.
        dated(given["key"], "2026-10-14", "dr-date-1")
        dated(given["key"], "2026-10-15", "dr-date-1", status=409)
        dated(given["key"], "2026-10-14")
        self.assertEqual(stored(), [(self.s507, "2026-10-14")])
        # Refused: a day before today, a malformed day, a key not on the list, fields it does not take.
        dated(given["key"], "2026-10-11", status=400)
        dated(given["key"], "2026-10-1", status=400)
        dated(given["key"], "14/10/2026", status=400)
        dated(f"{self.item('Quiz 2')}:{self.s507}", "2026-10-14", status=400)
        self.assertEqual(self.client.post(f"/api/scheduling/terms/{self.term}/dr-quizzes/dr-date", headers=HEADERS,
                                          json={"request_id": "x-1", "key": given["key"], "date": "2026-10-14", "time": "9:00"}).status_code, 400)
        self.assertEqual(self.client.post("/api/scheduling/terms/no-such-term/dr-quizzes/dr-date", headers=HEADERS,
                                          json={"request_id": "x-2", "key": given["key"], "date": "2026-10-14"}).status_code, 404)
        # Clearing returns the row to "Given in class"; the class schedule never changed.
        cleared = self.row("Quiz 3", 507, dated(given["key"], ""))
        self.assertEqual((cleared["date"], cleared["dr_date"], cleared["ready"]), ("2026-10-09", "", False))
        self.assertEqual([i["date"] for i in self.plan()["items"] if i["title"] == "Quiz 3"], ["2026-10-09"])
        self.assertEqual(stored(), [(self.s507, "2026-10-14"), (self.s507, "")])
        # A DR date for an upcoming quiz moves only its DR row; one that has passed is no longer listed.
        self.changed(set=[{"kind": "quiz", "title": "Quiz 4", "date": "2026-10-16"}])
        upcoming = self.row("Quiz 4", 508)
        moved = self.row("Quiz 4", 508, dated(upcoming["key"], "2026-10-19"))
        self.assertEqual((moved["date"], moved["class_date"], moved["start"]), ("2026-10-19", "2026-10-16", "09:10"))
        from scheduling import clock
        with mock.patch.object(clock, "campus_today", return_value=clock.dt.date(2026, 10, 20)):
            self.assertEqual([(q["title"], q["section"]) for q in self.read()["quizzes"]], [("Quiz 3", 507), ("Quiz 3", 508), ("Quiz 4", 507)])
        with self.db() as db:
            self.assertRaises(Exception, db.execute, "UPDATE dr_quiz_sittings SET date=''")
            self.assertRaises(Exception, db.execute, "DELETE FROM dr_quiz_sittings")

    def test_assistant_reads_dr_quizzes(self):
        from test_assistant import settle
        self.addCleanup(settle)
        self.use()
        self.bookings(AIM)
        self.takers(ALICE)
        self.changed(set=[{"kind": "quiz", "title": "Quiz 5", "date": "2026-10-23"}])
        assistant = self.app.extensions["assistant"]
        self.assertIn("dr_quizzes", assistant.tools)
        text, event = assistant.run_tool(assistant.open_session("test"), "dr_quizzes", {"term_id": self.term})
        self.assertTrue(event["ok"], text)
        answer = json.loads(text)
        answer.pop("_evidence", None)
        self.assertEqual(answer, self.read())
        self.assertEqual(answer["quizzes"][0]["aim"]["status"], "in_aim")
