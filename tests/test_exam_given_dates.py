"""EXAMS "DATE GIVEN" for a SECTION (exam_workflow/given_dates.py `term_given` / `section_given`, SCHEDULING "CLASS SCHEDULE",
2026-10-10): the day, time and place an assessment is given to each section, read from its seating charts and the exam record's
main sitting by the same rule as the per-student `given_dates`, with which record gave each."""
from test_scheduling_class_schedule import ClassScheduleBase

from exam_workflow.given_dates import given_dates, section_given, term_given


class SectionGivenTests(ClassScheduleBase):
    def read(self, assignment=None):
        with self.db() as db:
            return term_given(db, self.term, assignment)

    def test_a_quiz_is_its_main_sitting_for_every_section_listed(self):
        quiz, _ = self.assessment("quiz-4", "Quiz 4", "2026-10-14", "8:00 AM", "HELD 107", sections=(507,))
        undated, _ = self.assessment("quiz-5", "Quiz 5")
        found = self.read()
        self.assertEqual(sorted(found[quiz]), [self.s507])  # only the sections the record lists
        fact = found[quiz][self.s507]
        self.assertEqual((fact["date"], fact["time"], fact["place"], fact["source"], fact["time_source"], fact["place_source"]),
                         ("2026-10-14", "8:00 AM", "HELD 107", "sitting", "sitting", "sitting"))
        self.assertEqual({(f["date"], f["source"]) for f in found[undated].values()}, {("", "")})  # a record without a date says none
        with self.db() as db:
            self.assertEqual(section_given(db, self.term, quiz, self.s507)["date"], "2026-10-14")
            self.assertIsNone(section_given(db, self.term, quiz, self.s508))
            self.assertIsNone(section_given(db, self.term, "no-such-assignment", self.s507))
        self.assertEqual(sorted(self.read(quiz)), [quiz])  # narrowed to one assessment

    def test_a_chart_gives_its_sections_their_own_day_time_and_room(self):
        exam, event = self.assessment("exam-2", "Exam 2", "2026-10-29", "7:30 PM–9:30 PM", "Rudder")
        self.chart(exam, 507, "ILCB 111")  # no date yet: it still says the room
        self.chart(exam, 508, "HELD 100", "2026-10-30", "")  # its own day; no time of its own
        found = self.read(exam)[exam]
        mine, theirs = found[self.s507], found[self.s508]
        self.assertEqual((mine["date"], mine["time"], mine["place"], mine["source"], mine["time_source"], mine["place_source"]),
                         ("2026-10-29", "7:30 PM–9:30 PM", "ILCB 111", "sitting", "sitting", "chart"))
        # Another day than the main sitting: the sitting's time and place are not this section's.
        self.assertEqual((theirs["date"], theirs["time"], theirs["place"], theirs["source"], theirs["time_source"]),
                         ("2026-10-30", "", "HELD 100", "chart", ""))
        # A chart with its own time on the day it shares with the main sitting keeps its own time.
        with self.db() as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-10-29', exam_time='6:00 PM' WHERE assignment_id=? AND exam_date=''", (exam,))
        again = self.read(exam)[exam][self.s507]
        self.assertEqual((again["date"], again["time"], again["source"], again["time_source"]), ("2026-10-29", "6:00 PM", "chart", "chart"))

    def test_a_dr_exams_chart_and_a_makeups_chart_never_give_the_day(self):
        exam, event = self.assessment("exam-2", "Exam 2", "2026-10-29", "7:30 PM")
        dr = self.chart(exam, 507, "DR CENTER", "2026-10-27", "9:00 AM")
        makeup = self.chart(exam, 508, "BLOC 164", "2026-11-05", "1:00 PM")
        event = self.exams.get(event["id"])
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Makeup", "kind": "makeup", "date": "2026-11-05"})
        sitting = next(s["id"] for s in event["sittings"] if s["kind"] == "makeup")
        with self.db() as db:
            db.execute("UPDATE seating_charts SET dr_exam_key='dr-key' WHERE id=?", (dr["id"],))
            db.execute("INSERT INTO exam_event_charts(event_id, chart_id, original_chart_id, sitting_id, created_at) VALUES (?,?,?,?,?)",
                       (event["id"], makeup["id"], makeup["id"], sitting, "2026-10-01T00:00:00+00:00"))
        found = self.read(exam)[exam]
        self.assertEqual({(f["date"], f["source"], f["place"]) for f in found.values()}, {("2026-10-29", "sitting", "")})
        # The per-student reading agrees for everyone without a sitting of their own, with and without a makeup's.
        with self.db() as db:
            members = [tuple(row) for row in db.execute("SELECT section_id, enrollment_id FROM exam_participants WHERE event_id=?", (event["id"],))]
            self.assertTrue(members)
            for own in (True, False):
                for key, day in given_dates(db, event["id"], self.term, exam, members, own_makeup=own).items():
                    self.assertEqual(day, found[key[0]]["date"])

    def test_charts_alone_give_a_day_before_any_exam_record(self):
        assignment = self.documents.create_assignment(self.definition, "final", "Final exam")["id"]
        self.chart(assignment, 507, "HELD 107", "2026-12-11", "8:00 AM–10:00 AM")
        found = self.read()[assignment]
        self.assertEqual(sorted(found), [self.s507])
        self.assertEqual((found[self.s507]["date"], found[self.s507]["time"], found[self.s507]["place"], found[self.s507]["event_id"]),
                         ("2026-12-11", "8:00 AM–10:00 AM", "HELD 107", ""))
