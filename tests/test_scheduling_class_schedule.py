"""SCHEDULING "CLASS SCHEDULE" (scheduling/class_schedule.py, 2026-10-10) on a scratch workspace: a section's class schedule read
from the records that own each date (class days, the day a quiz or exam is given with its time and place, gradebook and Canvas-copy
due dates), the term's plan (planned rows with a section's own values, class-day topics, the record a planned row follows), the
four disagreement rules, and that planned rows reach no other page."""
import datetime as dt
import hashlib
import json
import uuid
from unittest import mock

from test_rooms import chart_bytes
from test_scheduling_service import CLASS, ScheduleBase
from test_storage import HEADERS

from assistant import policy
from rosters import connect
from scheduling import class_schedule

FIRST, LAST = "2026-08-24", "2026-12-03"


def instant(day, clock="23:59"):
    from canvas.placement import campus_instant
    return campus_instant(day, clock)


class ClassScheduleBase(ScheduleBase):
    def setUp(self):
        super().setUp()
        self.documents, self.exams = self.app.extensions["documents"], self.app.extensions["absences"].exams
        self.s507, self.s508 = self.sections[507], self.sections[508]
        patcher = mock.patch("scheduling.clock.campus_today", return_value=dt.date(2026, 10, 12))
        patcher.start()
        self.addCleanup(patcher.stop)

    def db(self):
        return connect(self.app.config["DATABASE"])

    def use(self, days_off=("2026-09-07",)):
        """Both sections meet Mon/Wed/Fri, and the term uses its Term schedule."""
        saved = self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": 0, "first_day": FIRST, "last_day": LAST, "days_off": list(days_off),
            "entries": [{**CLASS, "section_id": self.s507}, {**CLASS, "section_id": self.s508, "start": "09:10", "end": "10:00"}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        url = f"/api/scheduling/terms/{self.term}/use"
        used = self.client.post(url, headers=HEADERS, json={"request_id": str(uuid.uuid4()), "confirmation": "use",
                                                            "fingerprint": self.client.get(url).json["fingerprint"]})
        self.assertEqual(used.status_code, 200, used.json)

    def schedule(self, section=507, **query):
        response = self.client.get(f"/api/scheduling/sections/{self.sections[section]}/class-schedule", query_string=query)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def term_view(self, section=507):
        response = self.client.get(f"/api/scheduling/groups/{self.group}/sections/{section}/class-schedule")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def plan(self):
        return self.client.get(f"/api/scheduling/terms/{self.term}/class-schedule").json

    def change(self, request=None, base=None, **body):
        base = self.plan()["revision"] if base is None else base
        return self.client.post(f"/api/scheduling/terms/{self.term}/class-schedule", headers=HEADERS,
                                json={"request_id": request or str(uuid.uuid4()), "base_revision": base, **body})

    def changed(self, **body):
        response = self.change(**body)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def link(self, item, request=None, **target):
        return self.client.post(f"/api/scheduling/terms/{self.term}/class-schedule/links", headers=HEADERS,
                                json={"request_id": request or str(uuid.uuid4()), "item": item, **target})

    def day(self, view, date):
        return next((day for day in view["days"] if day["date"] == date), None)

    def work(self, view, date, title=None):
        day = self.day(view, date) or {"work": []}
        return [row for row in day["work"] if title in (None, row["title"])]

    def assessment(self, key, title, date="", time="", location="", sections=(507, 508)):
        """A course-wide assignment with the term's exam record, its main sitting dated when `date` is given."""
        assignment = self.documents.create_assignment(self.definition, key, title)
        event = self.exams.create({"term_id": self.term, "assignment_id": assignment["id"], "section_ids": [self.sections[n] for n in sections]})
        if date:
            event = self.exams.save_sitting(event["id"], {"version": event["version"], "label": title, "kind": "main", "date": date,
                                                          "time": time, "location": location})
        return assignment["id"], event

    def chart(self, assignment, section, room, date="", time=""):
        rooms = self.app.extensions["rooms"]
        found = next((r for r in rooms.list() if r["name"] == room), None)
        found = found or rooms.create(room, "", chart_bytes({"A1": "A1", "B1": "A2"}), "room.xlsx")
        chart = self.app.extensions["seating"].create({"assignment_id": assignment, "section_ids": [self.sections[section]], "room_id": found["id"], "notes": ""})
        with self.db() as db:  # seeding the date does not confirm a chart or issue cards
            db.execute("UPDATE seating_charts SET exam_date=?,exam_time=? WHERE id=?", (date, time, chart["id"]))
        return chart

    def copy(self, *assignments):
        """Faculty's Canvas copy holding these assignments (a revision written straight into the scratch catalog)."""
        content = {"modules": [], "pages": [], "assignments": [{
            "id": f"copy-{n}", "name": a["name"], "description": "", "points": "10", "grading_type": "points", "submission_types": ["none"],
            "external": False, "group": "Homework", "due_at": a.get("due_at"), "section_due": a.get("section_due", {}), "unlock_at": None,
            "lock_at": None, "published": True, "omit_from_final_grade": False, "gradebook_item": a.get("gradebook_item"),
            "faculty_assignment": a.get("faculty_assignment"), "sections": a.get("sections", [])} for n, a in enumerate(assignments)]}
        text = json.dumps(content)
        with self.db() as db:
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM canvas_content_revisions WHERE term_id=?", (self.term,)).fetchone()[0]
            db.execute("INSERT INTO canvas_content_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, self.term, revision, text, hashlib.sha256(text.encode()).hexdigest(), uuid.uuid4().hex, "0" * 64,
                        "test", "2026-10-01T00:00:00+00:00"))

    def canvas_course(self, section, course):
        with self.db() as db:
            db.execute("INSERT INTO canvas_course_links VALUES (?,?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, self.sections[section], str(course), f"MATH 140 {section}", f"MATH 140 {section}", "Fall 2026", "test",
                        "2026-08-20T00:00:00+00:00"))

    def columns(self, *items):
        return self.setup_gradebook([{"title": title, "points": "10", **more} for title, more in items])


class ReadTests(ClassScheduleBase):
    def test_class_days_are_what_the_calendar_shows_for_the_section(self):
        empty = self.schedule()
        self.assertEqual((empty["days"], empty["term_schedule"]["in_use"], empty["revision"]), ([], False, 0))
        self.assertEqual((empty["start"], empty["end"], empty["today"]), ("2026-09-28", "2026-11-09", "2026-10-12"))
        # Before the term uses its schedule, the per-date class events are the class days; a saved revision is not read.
        calendar = self.app.extensions["calendar"]
        for day in ("2026-10-12", "2026-10-13"):
            calendar.create({"request_id": str(uuid.uuid4()), "title": "MATH 140 · Section 507", "date": day, "time": "08:00", "end_time": "08:50",
                             "all_day": False, "location": "HELD 107", "scope": "section", "definition_id": self.definition, "term_id": self.term,
                             "section_id": self.s507})
        self.assertEqual([d["date"] for d in self.schedule()["days"]], ["2026-10-12", "2026-10-13"])
        self.use(days_off=("2026-09-07", "2026-10-14"))
        view = self.schedule()
        self.assertTrue(view["term_schedule"]["in_use"])
        # Mon Oct 12 was replaced by the schedule's own row; Tue Oct 13 (a day run on another day's schedule) still shows; Wed is off.
        self.assertEqual(self.day(view, "2026-10-12")["meetings"], [{"time": "08:00", "end_time": "08:50", "room": "HELD 107"}])
        self.assertEqual(self.day(view, "2026-10-13")["meetings"], [{"time": "08:00", "end_time": "08:50", "room": "HELD 107"}])
        self.assertEqual((self.day(view, "2026-10-14")["off"], self.day(view, "2026-10-14")["meetings"]), (True, []))
        self.assertEqual(self.day(view, "2026-10-16")["weekday"], "Fri")
        self.assertIsNone(self.day(view, "2026-10-15"))  # nothing on a Thursday
        self.assertEqual(self.day(self.schedule(508), "2026-10-12")["meetings"][0]["time"], "09:10")
        self.assertIsNone(self.day(self.schedule(508), "2026-10-13"))
        whole = self.term_view()
        self.assertEqual((whole["days"][0]["date"], whole["days"][-1]["date"]), ("2026-08-24", "2026-12-02"))
        self.assertEqual([s["number"] for s in whole["sections"]], [507, 508])
        self.assertLessEqual(len(view["days"]), 43)  # the default window stays under the Assistant's 60-item trim
        asked = self.schedule(start="2026-09-01", end="2026-09-08")
        self.assertEqual([(d["date"], d["off"]) for d in asked["days"]], [("2026-09-02", False), ("2026-09-04", False), ("2026-09-07", True)])
        self.assertEqual(self.client.get(f"/api/scheduling/sections/{self.s507}/class-schedule?start=2026-09-01").status_code, 400)
        self.assertEqual(self.client.get("/api/scheduling/sections/nope/class-schedule").status_code, 404)

    def test_a_quiz_an_exam_and_a_final_are_read_for_each_section(self):
        self.use()
        quiz, _ = self.assessment("quiz-4", "Quiz 4", "2026-10-14", "8:00 AM", "HELD 107")
        exam, _ = self.assessment("exam-2", "Exam 2", "2026-10-29", "7:30 PM–9:30 PM")
        self.chart(exam, 507, "ILCB 111")
        self.chart(exam, 508, "HELD 100")
        final, _ = self.assessment("final", "Final exam")
        self.chart(final, 507, "HELD 107", "2026-12-11", "8:00 AM–10:00 AM")
        self.chart(final, 508, "HELD 107", "2026-12-14", "10:30 AM–12:30 PM")
        view, other = self.term_view(507), self.term_view(508)
        row = self.work(view, "2026-10-14")[0]
        self.assertEqual((row["title"], row["what"], row["time"], row["place"], row["source"], row["planned"], row["key"]),
                         ("Quiz 4", "given", "8:00 AM", "HELD 107", "exam", False, f"a:{quiz}"))
        self.assertTrue(row["href"].startswith("#exams/"))
        # A regular exam: the main sitting's day and time, each section its own room from its chart (which has no date yet).
        mine, theirs = self.work(view, "2026-10-29")[0], self.work(other, "2026-10-29")[0]
        self.assertEqual((mine["time"], mine["place"], theirs["time"], theirs["place"]), ("7:30 PM–9:30 PM", "ILCB 111", "7:30 PM–9:30 PM", "HELD 100"))
        # A final: each section its own day, time and place, after the last class day.
        self.assertEqual([(r["time"], r["place"], r["source"]) for r in self.work(view, "2026-12-11")], [("8:00 AM–10:00 AM", "HELD 107", "seating")])
        self.assertEqual(self.work(view, "2026-12-14"), [])
        self.assertEqual([(r["time"], r["place"]) for r in self.work(other, "2026-12-14")], [("10:30 AM–12:30 PM", "HELD 107")])
        self.assertTrue(self.work(view, "2026-12-11")[0]["href"].startswith("#seating/"))
        self.assertEqual((view["differs"], view["undated"], view["open_planned"]), ([], [], 0))
        # The section reading agrees with the per-student one for everyone without a sitting of their own.
        from exam_workflow.given_dates import given_dates, term_given
        with self.db() as db:
            read = term_given(db, self.term)
            for assignment, event in ((quiz, None), (exam, None), (final, None)):
                event = db.execute("SELECT id FROM exam_events WHERE assignment_id=?", (assignment,)).fetchone()[0]
                members = db.execute("SELECT section_id, enrollment_id FROM exam_participants WHERE event_id=?", (event,)).fetchall()
                self.assertTrue(members)
                for key, date in given_dates(db, event, self.term, assignment, [tuple(m) for m in members]).items():
                    self.assertEqual(date or "", read[assignment][key[0]]["date"])

    def test_due_dates_come_from_the_gradebook_and_the_canvas_copy(self):
        self.use()
        ids = self.columns(("HW 5", {"due_date": "2026-10-18"}), ("HW 6", {"due_date": "2026-10-25", "sections": [self.s508]}),
                           ("Old", {"due_date": "2026-10-20", "archived": True}))
        view = self.term_view()
        row = self.work(view, "2026-10-18")[0]  # a Sunday: a date without a class
        self.assertEqual((row["title"], row["what"], row["source"], row["href"], self.day(view, "2026-10-18")["meetings"]),
                         ("HW 5", "due", "gradebook", f"#gradebook/{self.term}?item={ids['HW 5']}", []))
        self.assertEqual((self.work(view, "2026-10-25"), self.work(view, "2026-10-20")), ([], []))  # another section's; archived
        self.assertEqual(self.work(self.term_view(508), "2026-10-25")[0]["title"], "HW 6")
        # Faculty's Canvas copy: this section's own date where it has one. The column carries the LATEST of its sections' dates.
        self.canvas_course(507, 111)
        self.canvas_course(508, 222)
        self.copy({"name": "HW 5", "gradebook_item": ids["HW 5"], "due_at": instant("2026-10-18"), "section_due": {self.s507: instant("2026-10-16")}},
                  {"name": "Syllabus quiz", "due_at": instant("2026-10-19")})
        view, other = self.term_view(507), self.term_view(508)
        mine = self.work(view, "2026-10-16")[0]
        self.assertEqual((mine["title"], mine["source"], mine["time"], mine["differs"]), ("HW 5", "canvas", "23:59", []))
        self.assertEqual(self.work(view, "2026-10-18"), [])
        self.assertEqual((self.work(other, "2026-10-18")[0]["source"], other["differs"]), ("canvas", []))
        self.assertEqual(self.work(view, "2026-10-19")[0]["key"], "c:copy-1")  # an assignment only the copy holds
        self.assertEqual(view["differs"], [])


class RuleTests(ClassScheduleBase):
    def rules(self, view):
        return [(d["title"], d["rule"]) for d in view["differs"]]

    def test_the_gradebook_and_the_canvas_copy_disagree_only_when_the_column_is_not_the_latest(self):
        self.use()
        ids = self.columns(("HW 5", {"due_date": "2026-10-12"}), ("HW 6", {"due_date": "2026-10-25"}))
        self.canvas_course(507, 111)
        self.canvas_course(508, 222)
        self.copy({"name": "HW 5", "gradebook_item": ids["HW 5"], "due_at": instant("2026-10-12"), "section_due": {self.s508: instant("2026-10-14")}},
                  {"name": "HW 6", "gradebook_item": ids["HW 6"], "due_at": instant("2026-10-23"), "section_due": {self.s508: instant("2026-10-25")}})
        # HW 5: the column says Oct 12 but 508's Canvas date is Oct 14, so 508's blanks would count zero two days early; 507 agrees.
        # HW 6: the column carries the later section's date, as a column does; neither section is marked.
        self.assertEqual(self.rules(self.term_view(507)), [])
        view = self.term_view(508)
        self.assertEqual(self.rules(view), [("HW 5", "gradebook_canvas")])
        found = view["differs"][0]
        self.assertEqual((found["dates"], found["date"]), ({"gradebook": "2026-10-12", "canvas": "2026-10-14"}, "2026-10-14"))
        self.assertEqual(found["text"], "The gradebook says due Mon, Oct 12; Faculty's Canvas copy says Wed, Oct 14.")
        self.assertEqual(found["hrefs"], {"gradebook": f"#gradebook/{self.term}?item={ids['HW 5']}", "canvas": f"#course/{self.group}/home/canvas"})
        self.assertEqual(self.work(view, "2026-10-14")[0]["differs"], ["gradebook_canvas"])
        # A window that leaves the date out leaves its disagreement out.
        self.assertEqual(self.schedule(508, start="2026-11-01", end="2026-11-30")["differs"], [])

    def test_the_day_given_against_its_due_dates(self):
        self.use()
        quiz, _ = self.assessment("quiz-4", "Quiz 4", "2026-10-14")
        late, _ = self.assessment("quiz-5", "Quiz 5", "2026-10-21")
        final, _ = self.assessment("final", "Final exam")
        self.chart(final, 507, "HELD 107", "2026-12-11", "8:00 AM")
        self.chart(final, 508, "HELD 107", "2026-12-14", "8:00 AM")
        ids = self.columns(("Quiz 4", {"assignment_id": quiz, "due_date": "2026-10-14"}), ("Quiz 5", {"assignment_id": late, "due_date": "2026-10-23"}),
                           ("Final exam", {"assignment_id": final, "due_date": "2026-12-14"}))
        self.copy({"name": "Quiz 5", "gradebook_item": ids["Quiz 5"], "faculty_assignment": late, "due_at": instant("2026-10-22")})
        view = self.term_view(507)
        # Quiz 4 is due the day it is given: one line, nothing marked. The final's one column carries the later section's day.
        self.assertEqual([(r["what"], r["differs"]) for r in self.work(view, "2026-10-14")], [("given", [])])
        self.assertEqual(self.rules(view), [("Quiz 5", "gradebook_canvas"), ("Quiz 5", "given_gradebook"), ("Quiz 5", "given_canvas")])
        self.assertEqual([r["what"] for r in self.work(view, "2026-10-21")], ["given"])
        self.assertEqual([(r["what"], r["source"]) for r in self.work(view, "2026-10-22")], [("due", "canvas")])
        self.assertEqual(view["differs"][1]["text"], "Given Wed, Oct 21; the gradebook says due Fri, Oct 23.")
        self.assertEqual(view["differs"][2]["text"], "Given Wed, Oct 21; Faculty's Canvas copy says due Thu, Oct 22.")
        # A planned row that records a due date apart from the day says so on purpose: the day given is no longer compared with
        # them (the gradebook and the copy still disagree with each other).
        row = self.changed(set=[{"kind": "quiz", "title": "Quiz 5 plan", "date": "2026-10-21", "due_date": "2026-10-23"}])["items"][0]
        self.assertEqual(self.link(row["item"], assignment_id=late).status_code, 200)
        self.assertEqual(self.rules(self.term_view(507)), [("Quiz 5", "gradebook_canvas")])

    def test_certified_work_is_listed_and_never_marked(self):
        self.use()
        late, _ = self.assessment("quiz-5", "Quiz 5", "2026-10-21")
        self.columns(("Quiz 5", {"assignment_id": late, "due_date": "2026-10-23"}))
        self.assertEqual(self.rules(self.term_view()), [("Quiz 5", "given_gradebook")])
        with mock.patch("certified_archive.certified", return_value={(late, self.term): {"plan_id": "p", "certification_id": "c", "certified_at": "x"}}):
            view = self.term_view()
        self.assertEqual((self.rules(view), self.work(view, "2026-10-21")[0]["archived"], self.work(view, "2026-10-21")[0]["differs"]), ([], True, []))


class PlanTests(ClassScheduleBase):
    def test_planned_rows_are_the_terms_with_a_sections_own_values(self):
        self.use()
        self.assertEqual((self.plan()["revision"], self.plan()["items"], self.plan()["topics"]), (0, [], {}))
        saved = self.change(request="first", set=[
            {"kind": "quiz", "title": "  Quiz 6 ", "date": "2026-10-23"},
            {"kind": "exam", "title": "Exam 3", "date": "2026-11-17", "time": "7:30 PM–9:30 PM",
             "section_values": {self.s507: {"place": "ILCB 111"}, self.s508: {"place": "HELD 100"}}},
            {"kind": "final", "title": "Final exam", "section_values": {
                self.s507: {"date": "2026-12-11", "time": "8:00 AM–10:00 AM", "place": "HELD 107"},
                self.s508: {"date": "2026-12-14", "time": "10:30 AM–12:30 PM", "place": "BLOC 149"}}},
            {"kind": "homework", "title": "HW 9", "date": "2026-11-01", "sections": [self.s508]},
            {"kind": "other", "title": "Q-drop deadline", "date": "2026-11-13", "sections": [self.s507, self.s508]},
            {"kind": "exam", "title": "Makeup day"}])
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual((saved.json["revision"], [i["title"] for i in saved.json["items"]]),
                         (1, ["Quiz 6", "HW 9", "Q-drop deadline", "Exam 3", "Final exam", "Makeup day"]))
        self.assertTrue(all(len(i["item"]) == 16 and i["link"] is None for i in saved.json["items"]))
        self.assertEqual(next(i for i in saved.json["items"] if i["title"] == "Q-drop deadline")["sections"], [])  # every section: stored as none
        view, other = self.term_view(507), self.term_view(508)
        quiz = self.work(view, "2026-10-23")[0]
        self.assertEqual((quiz["what"], quiz["planned"], quiz["source"], quiz["linked"], quiz["href"], quiz["kind"]),
                         ("given", True, "planned", False, None, "quiz"))
        self.assertEqual((self.work(view, "2026-11-17")[0]["place"], self.work(other, "2026-11-17")[0]["place"]), ("ILCB 111", "HELD 100"))
        self.assertEqual([(r["time"], r["place"]) for r in self.work(view, "2026-12-11")], [("8:00 AM–10:00 AM", "HELD 107")])
        self.assertEqual([(r["time"], r["place"]) for r in self.work(other, "2026-12-14")], [("10:30 AM–12:30 PM", "BLOC 149")])
        self.assertEqual((self.work(view, "2026-11-01"), self.work(other, "2026-11-01")[0]["what"]), ([], "due"))
        self.assertEqual(self.work(view, "2026-11-13")[0]["what"], "date")
        self.assertEqual(([r["title"] for r in view["undated"]], view["open_planned"], other["open_planned"]), (["Makeup day"], 5, 6))
        self.assertEqual(view["revision"], 1)
        # The same request answers the same; its ID with another change, or a stale revision, is refused; identical records nothing.
        self.assertEqual(self.change(request="first", base=0, set=[{"kind": "quiz", "title": "Quiz 6", "date": "2026-10-23"}]).status_code, 409)
        self.assertEqual(self.change(base=0, set=[{"kind": "quiz", "title": "Quiz 7", "date": "2026-10-30"}]).status_code, 409)
        same = self.changed(set=[{k: v for k, v in saved.json["items"][0].items()}])
        self.assertEqual(same["revision"], 1)
        # An edit keeps the row's identity; a removal takes it off; the identity stays in the catalog.
        first = saved.json["items"][0]
        edited = self.changed(set=[{**first, "date": "2026-10-26"}], remove=[saved.json["items"][-1]["item"]])
        self.assertEqual((edited["revision"], len(edited["items"]), next(i for i in edited["items"] if i["item"] == first["item"])["date"]),
                         (2, 5, "2026-10-26"))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scheduling_plan_items").fetchone()[0], 6)
        self.assertEqual(policy.base_rule("scheduling.class_plan_save").tier, "approve")
        self.assertEqual(policy.base_rule("scheduling.class_plan_link").tier, "approve")

    def test_what_a_plan_refuses(self):
        self.use()
        self.changed(set=[{"kind": "quiz", "title": "Quiz 6", "date": "2026-10-23"}])
        cases = [
            ({"set": [{"kind": "midterm", "title": "X"}]}, "a quiz, an exam"),
            ({"set": [{"kind": "quiz", "title": " "}]}, "a title"),
            ({"set": [{"kind": "quiz", "title": "quiz 6", "date": "2026-10-30"}]}, "share a title"),
            ({"set": [{"kind": "quiz", "title": "Q", "date": "10/23"}]}, "YYYY-MM-DD"),
            ({"set": [{"kind": "quiz", "title": "Q", "date": "2027-03-01"}]}, "within a month"),
            ({"set": [{"kind": "quiz", "title": "Q", "time": "after lunch"}]}, "a clock or a range"),
            ({"set": [{"kind": "homework", "title": "H", "date": "2026-10-23", "due_date": "2026-10-25"}]}, "Only a quiz or an exam"),
            ({"set": [{"kind": "quiz", "title": "Q", "sections": ["nope"]}]}, "sections of this term"),
            ({"set": [{"kind": "quiz", "title": "Q", "sections": [self.s507], "section_values": {self.s508: {"place": "X"}}}]}, "a section the row is for"),
            ({"set": [{"kind": "quiz", "title": "Q", "section_values": {self.s507: {"room": "X"}}}]}, "own date, time and place"),
            ({"set": [{"kind": "quiz", "title": "Q", "item": "0000000000000000"}]}, "not one of this term's"),
            ({"set": [{"kind": "quiz", "title": "Q", "seats": 3}]}, "its kind, title"),
            ({"remove": ["0000000000000000"]}, "not one of this term's"),
            ({"remove": [{}]}, "not one of this term's"),
            ({"set": [{"kind": "quiz", "title": "Q", "item": []}]}, "not one of this term's"),
            ({"set": [{"kind": "quiz", "title": "Q", "sections": [{}]}]}, "sections of this term"),
            ({"topics": {"2026-10-12": {"notes": [[]]}}}, "by number"),
            ({"topics": {"2026-10-12": {"notes": ["two"]}}}, "by number"),
            ({"topics": {"2026-10-12": {"notes": ["2.1"] * 2}}}, "by number"),
            ({"topics": {"10-12": {"notes": ["2.1"]}}}, "YYYY-MM-DD"),
            ({"topics": {"2026-10-12": {"notes": [], "sections": {"nope": {"notes": ["2.1"]}}}}}, "a section of this term"),
            ({"rows": []}, "Send request_id"),
        ]
        for body, why in cases:
            refused = self.change(**body)
            self.assertEqual(refused.status_code, 400, (body, refused.json))
            self.assertIn(why, refused.json["error"], body)
        self.assertEqual(self.client.post(f"/api/scheduling/terms/{self.term}/class-schedule", headers=HEADERS,
                                          json={"base_revision": 1, "set": []}).status_code, 400)
        self.assertEqual(self.plan()["revision"], 1)

    def test_topics_are_the_terms_by_date_with_a_sections_own(self):
        self.use()
        saved = self.changed(topics={"2026-10-12": {"notes": ["3.2", "3.3"], "note": "Review"},
                                     "2026-10-14": {"notes": ["9.9"], "sections": {self.s508: {"notes": [], "note": "Catch up on 3.3"}}},
                                     "2026-10-13": {"notes": [], "note": ""}})
        self.assertEqual(sorted(saved["topics"]), ["2026-10-12", "2026-10-14"])  # a day that says nothing has no entry
        view, other = self.schedule(507), self.schedule(508)
        mine = self.day(view, "2026-10-12")["topics"]
        self.assertEqual(([n["number"] for n in mine["notes"]], mine["note"], mine["own"], mine["notes"][0]["known"]), (["3.2", "3.3"], "Review", False, False))
        self.assertEqual((self.day(view, "2026-10-14")["topics"]["notes"][0]["number"], self.day(other, "2026-10-14")["topics"]),
                         ("9.9", {"notes": [], "note": "Catch up on 3.3", "own": True}))
        # One day changes without sending the rest; None takes a day's entry away.
        again = self.changed(topics={"2026-10-12": None, "2026-10-16": {"notes": ["3.4"]}})
        self.assertEqual((sorted(again["topics"]), again["revision"]), (["2026-10-14", "2026-10-16"], 2))
        # A number in the course's notes outline shows its title; one the notes lack is kept as written.
        with mock.patch("course_notes.service.current_sections", return_value={"3.4": {"title": "Matrix inverses", "chapter": "3", "chapter_title": "", "url": "/x"}}):
            found = self.day(self.schedule(507), "2026-10-16")["topics"]["notes"]
        self.assertEqual(found, [{"number": "3.4", "title": "Matrix inverses", "known": True, "url": "/x"}])

    def test_a_planned_row_follows_the_record_it_becomes(self):
        self.use()
        rows = self.changed(set=[
            {"kind": "quiz", "title": "Quiz 6", "date": "2026-10-23", "time": "8:00 AM"},
            {"kind": "homework", "title": "HW 9", "date": "2026-11-01"},
            {"kind": "final", "title": "Final exam", "section_values": {self.s507: {"date": "2026-12-11", "place": "HELD 107"},
                                                                         self.s508: {"date": "2026-12-14", "place": "BLOC 149"}}}])["items"]
        by = {r["title"]: r["item"] for r in rows}
        open_rows = self.client.get(f"/api/scheduling/terms/{self.term}/class-schedule/open").json["items"]
        self.assertEqual([(r["title"], r["match"]) for r in open_rows], [("Quiz 6", None), ("HW 9", None), ("Final exam", None)])
        quiz, _ = self.assessment("quiz-6", "Quiz 6")
        ids = self.columns(("HW 9", {"due_date": "2026-11-02"}))
        # A likely match is the term's one unlinked record of the same title.
        open_rows = self.client.get(f"/api/scheduling/terms/{self.term}/class-schedule/open?kind=quiz").json["items"]
        self.assertEqual([(r["title"], r["match"]) for r in open_rows], [("Quiz 6", {"assignment_id": quiz, "title": "Quiz 6"})])
        linked = self.link(by["Quiz 6"], request="l1", assignment_id=quiz)
        self.assertEqual(linked.status_code, 200, linked.json)
        self.assertEqual(next(i for i in linked.json["items"] if i["item"] == by["Quiz 6"])["link"], {"assignment_id": quiz})
        self.assertEqual(self.link(by["Quiz 6"], request="l1", assignment_id=quiz).status_code, 200)  # the same request again
        self.assertEqual(self.link(by["Quiz 6"], request="l1", gradebook_item=ids["HW 9"]).status_code, 409)
        self.assertEqual(self.link(by["HW 9"], assignment_id=quiz).status_code, 409)  # one record follows one row
        self.assertEqual(self.link(by["HW 9"], gradebook_item="nope").status_code, 400)
        self.assertEqual(self.link(by["HW 9"], assignment_id=quiz, gradebook_item=ids["HW 9"]).status_code, 400)
        self.assertEqual(self.link("0000000000000000", assignment_id=quiz).status_code, 400)
        self.assertEqual(self.link(by["HW 9"], gradebook_item=ids["HW 9"]).status_code, 200)
        # The record has no date yet: the planned day still shows, as planned, under the record's name.
        view = self.term_view()
        row = self.work(view, "2026-10-23")[0]
        self.assertEqual((row["linked"], row["planned"], row["key"], row["time"]), (True, True, f"a:{quiz}", "8:00 AM"))
        # Once the record has a date the row follows it, and says what was planned.
        event = self.exams.get(db_event(self, quiz))
        self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Quiz 6", "kind": "main", "date": "2026-10-26"})
        view = self.term_view()
        self.assertEqual(self.work(view, "2026-10-23"), [])
        row = self.work(view, "2026-10-26")[0]
        self.assertEqual((row["planned"], row["planned_was"], row["source"], row["time"], row["differs"]), (False, "2026-10-23", "exam", "8:00 AM", []))
        due = self.work(view, "2026-11-02")[0]
        self.assertEqual((due["title"], due["source"], due["planned_was"], due["linked"], view["open_planned"]), ("HW 9", "gradebook", "2026-11-01", True, 1))
        # A final with one "Given on" for every section: each section keeps its own planned day, and the difference is marked.
        final, event = self.assessment("final", "Final exam", "2026-12-10")
        self.assertEqual(self.link(by["Final exam"], assignment_id=final).status_code, 200)
        view = self.term_view(508)
        row = self.work(view, "2026-12-14")[0]
        self.assertEqual((row["planned"], row["place"], row["differs"]), (True, "BLOC 149", ["plan_given"]))
        self.assertEqual(view["differs"][0]["text"], "Section 508's own planned day is Mon, Dec 14; the exam record says Thu, Dec 10 for every section.")
        # Its seating chart is the section's own record: it wins, and nothing is marked.
        self.chart(final, 508, "HELD 100", "2026-12-15", "1:00 PM")
        view = self.term_view(508)
        row = self.work(view, "2026-12-15")[0]
        self.assertEqual((row["planned"], row["place"], row["source"], row["planned_was"], view["differs"]), (False, "HELD 100", "seating", "2026-12-14", []))
        # Unlinking brings the planned row back beside the record; a removed column leaves its row planned again.
        self.assertEqual(self.link(by["Quiz 6"], action="unlink").status_code, 200)
        view = self.term_view()
        self.assertEqual(sorted((r["title"], r["linked"]) for d in ("2026-10-23", "2026-10-26") for r in self.work(view, d)), [("Quiz 6", False), ("Quiz 6", False)])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scheduling_plan_links").fetchone()[0], 4)

    def test_planned_rows_reach_no_other_page(self):
        self.use()
        quiz, _ = self.assessment("quiz-4", "Quiz 4", "2026-10-14")
        reads = ("/api/calendar?start=2026-08-01&end=2026-12-31", "/api/state", f"/api/gradebook/terms/{self.term}",
                 f"/api/scheduling/terms/{self.term}", f"/api/exams/{db_event(self, quiz)}")
        before = [self.client.get(url).get_data() for url in reads]
        row = self.changed(set=[{"kind": "quiz", "title": "Quiz 6", "date": "2026-10-23"}], topics={"2026-10-12": {"notes": ["3.2"]}})["items"][0]
        self.assertEqual(self.link(row["item"], assignment_id=quiz).status_code, 200)
        self.assertEqual([self.client.get(url).get_data() for url in reads], before)

    def test_a_course_edit_cannot_detach_a_class_schedule(self):
        self.changed(set=[{"kind": "quiz", "title": "Quiz 6", "date": "2026-10-23", "sections": [self.s508]}])  # no Term schedule saved
        course = next(c for c in self.client.get("/api/state").json["courses"] if c["definition_id"] == self.definition)
        moved = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "term": "Spring"}, headers=HEADERS)
        self.assertEqual(moved.status_code, 409, moved.json)
        self.assertIn("detach a class schedule", moved.json["error"])
        dropped = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "sections": [507, 509]}, headers=HEADERS)
        self.assertEqual(dropped.status_code, 409, dropped.json)
        self.assertIn("detach a class schedule", dropped.json["error"])
        with self.db() as db:
            self.assertEqual(class_schedule.named_sections(db, self.term), {self.s508})


class AssistantTests(ClassScheduleBase):
    def setUp(self):
        super().setUp()
        from test_assistant import settle
        self.addCleanup(settle)
        self.assistant = self.app.extensions["assistant"]
        self.session = self.assistant.open_session("test")

    def tool(self, name, **args):
        text, event = self.assistant.run_tool(self.session, name, args)
        return json.loads(text), event

    def test_the_assistant_reads_a_sections_schedule_whole(self):
        self.use()
        self.assessment("quiz-4", "Quiz 4", "2026-10-14", "8:00 AM", "HELD 107")
        days = [d["date"] for d in self.term_view()["days"] if d["meetings"]]
        self.assertGreater(len(days), 40)  # a term of class days, each with topics
        self.changed(topics={day: {"notes": ["3.2", "3.3"], "note": "Worked examples from the notes"} for day in days})
        answer, event = self.tool("class_schedule", section_id=self.s507)
        self.assertTrue(event["ok"], answer)
        self.assertNotIn("_truncated", answer)  # two weeks back to four ahead fits the Assistant's read whole
        self.assertEqual((answer["section"], answer["start"], answer["end"], answer["revision"]), (507, "2026-09-28", "2026-11-09", 1))
        given = next(d for d in answer["days"] if d["date"] == "2026-10-14")
        self.assertEqual(([w["title"] for w in given["work"]], given["topics"]["note"]), (["Quiz 4"], "Worked examples from the notes"))
        later, _ = self.tool("class_schedule", section_id=self.s507, start="2026-11-16", end="2026-11-20")
        self.assertEqual([d["date"] for d in later["days"]], ["2026-11-16", "2026-11-18", "2026-11-20"])
        self.assertIn("class_schedule", self.assistant.tools)
        routes = {rule.endpoint: sorted(rule.methods - {"HEAD", "OPTIONS"}) for rule in self.app.url_map.iter_rules()
                  if rule.endpoint.startswith("scheduling.class_")}
        self.assertEqual(routes, {"scheduling.class_schedule": ["GET"], "scheduling.class_schedule_group": ["GET"], "scheduling.class_plan": ["GET"],
                                  "scheduling.class_plan_open": ["GET"], "scheduling.class_plan_save": ["POST"], "scheduling.class_plan_link": ["POST"],
                                  "scheduling.class_plan_preview": ["POST"]})
        self.assertEqual(policy.base_rule("scheduling.class_plan_preview").tier, "read")
        self.assertLessEqual({"scheduling.class_plan_save", "scheduling.class_plan_link"}, policy.REQUEST_IDS)

    def test_an_assistant_change_waits_for_approval_and_is_then_made(self):
        self.use()
        path = f"/api/scheduling/terms/{self.term}/class-schedule"
        result, _ = self.tool("faculty_write", method="POST", path=path, summary="Plans Quiz 6 for Friday, October 23.",
                              body={"base_revision": 0, "set": [{"kind": "quiz", "title": "Quiz 6", "date": "2026-10-23"}]})
        self.assertEqual(result["outcome"], "waiting_for_approval", result)
        self.assertEqual(self.plan()["revision"], 0)
        card = self.client.get("/api/assistant/actions").json["pending"][0]
        self.assertEqual(card["endpoint"], "scheduling.class_plan_save")
        done = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual((done.status_code, done.json["action"]["status"]), (200, "succeeded"), done.json)
        saved = self.plan()
        self.assertEqual((saved["revision"], [i["title"] for i in saved["items"]], saved["actor"]), (1, ["Quiz 6"], "assistant"))
        quiz, _ = self.assessment("quiz-6", "Quiz 6")
        linked, _ = self.tool("faculty_write", method="POST", path=path + "/links", summary="Quiz 6 on the schedule follows the new quiz.",
                              body={"item": saved["items"][0]["item"], "assignment_id": quiz})
        self.assertEqual(linked["outcome"], "waiting_for_approval", linked)
        card = self.client.get("/api/assistant/actions").json["pending"][0]
        done = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual((done.status_code, done.json["action"]["status"]), (200, "succeeded"), done.json)
        self.assertEqual(self.plan()["items"][0]["link"], {"assignment_id": quiz})

    def test_a_change_the_plan_would_refuse_never_becomes_a_card(self):
        """2026-10-28: a follow-up planned a second "In-class quiz" beside the Oct 16 one; the card was made and failed on Approve
        with "Two planned rows share a title". The plan's own preview now checks the change when the card would be made."""
        self.use()
        self.changed(set=[{"kind": "quiz", "title": "In-class quiz", "date": "2026-10-16"}])
        path = f"/api/scheduling/terms/{self.term}/class-schedule"
        result, _ = self.tool("faculty_write", method="POST", path=path, summary="Plans Friday's quiz for Section 508.",
                              body={"base_revision": 1, "set": [{"kind": "quiz", "title": "In-class quiz", "date": "2026-10-30",
                                                                 "sections": [self.s508]}]})
        self.assertEqual(result["outcome"], "refused_by_preview", result)
        self.assertIn('Two planned rows share a title: "In-class quiz" (already planned for 2026-10-16)', result["error"])
        self.assertEqual(self.client.get("/api/assistant/actions").json["pending"], [])
        # Named on its own it is a card whose preview shows the row; Approve sends the preview's fingerprint and saves it.
        result, _ = self.tool("faculty_write", method="POST", path=path, summary="Plans Quiz 6 for Section 508 on Friday.",
                              body={"base_revision": 1, "set": [{"kind": "quiz", "title": "Quiz 6", "date": "2026-10-30",
                                                                 "sections": [self.s508]}]})
        self.assertEqual(result["outcome"], "waiting_for_approval", result)
        card = self.client.get("/api/assistant/actions").json["pending"][0]
        self.assertEqual([(row["title"], row["date"], row["sections"]) for row in card["preview"]["data"]["set"]],
                         [("Quiz 6", "2026-10-30", [self.s508])])
        done = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual((done.status_code, done.json["action"]["status"]), (200, "succeeded"), done.json)
        self.assertEqual([i["title"] for i in self.plan()["items"]], ["In-class quiz", "Quiz 6"])
        # The preview writes nothing and checks as a save does; a save naming another change's fingerprint is refused.
        body = {"base_revision": 2, "remove": [self.plan()["items"][0]["item"]]}
        shown = self.client.post(path + "/preview", headers=HEADERS, json=body)
        self.assertEqual((shown.status_code, shown.json["remove"], shown.json["set"]), (200, ["In-class quiz"], []))
        self.assertEqual(self.client.post(path + "/preview", headers=HEADERS, json={**body, "base_revision": 1}).status_code, 409)
        self.assertEqual(self.change(base=2, set=[], fingerprint=shown.json["fingerprint"]).status_code, 409)
        self.assertEqual(self.plan()["revision"], 2)
        self.changed(remove=body["remove"], fingerprint=shown.json["fingerprint"])


def db_event(case, assignment):
    with case.db() as db:
        return db.execute("SELECT id FROM exam_events WHERE assignment_id=?", (assignment,)).fetchone()[0]
