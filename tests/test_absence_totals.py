"""ABSENCES "TOTAL ABSENCES" (absences/totals.py; requirement, 2026-10-31):
the days a student missed, read from their absences, their iClicker 0s (fake iClicker) and their quiz 0s, each day once."""
import datetime as dt
import uuid
from unittest import mock

from test_iclicker import Seeded
from test_scheduling_service import CLASS
from test_storage import HEADERS

from rosters import connect


class TotalAbsencesTests(Seeded):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("scheduling.clock.campus_today", return_value=dt.date(2026, 9, 20))
        patcher.start()
        self.addCleanup(patcher.stop)
        with connect(self.app.config["DATABASE"]) as db:
            self.definition = db.execute("SELECT definition_id FROM course_terms WHERE id=?", (self.term,)).fetchone()[0]
        self.assertEqual(self.link(507, self.course_a).status_code, 200)

    def rows(self):
        return {student["name"]: student["row"] for student in self.client.get(self.base).json["students"]}

    def imported(self, request_id):
        answer = self.apply(507, self.preview(507, {"missing_as_missing": True}), request_id)
        self.assertEqual(answer.status_code, 200, answer.json)

    def record(self, first, last, status="pending", name="Example, Bob"):
        section_id, enrollment_id = self.rows()[name].split(":")
        answer = self.client.post("/api/absences", headers=HEADERS, json={
            "section_id": section_id, "enrollment_id": enrollment_id, "first_date": first, "last_date": last, "reason": "illness",
            "status": status, "request_id": str(uuid.uuid4())})
        self.assertEqual(answer.status_code, 201, answer.json)
        return answer.json

    def total(self, name="Example, Bob"):
        section_id, enrollment_id = self.rows()[name].split(":")
        answer = self.client.get(f"/api/absences/students/{section_id}/{enrollment_id}")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json["total"]

    def days(self, name="Example, Bob"):
        return [(day["date"], day["state"], sorted(source["kind"] for source in day["sources"])) for day in self.total(name)["days"]]

    def quiz(self, key, title, date, scores):
        """A quiz: an exam record of both sections given on `date` (no seating chart), its column, and these students' scores."""
        assignment = self.app.extensions["documents"].create_assignment(self.definition, key, title)
        exams = self.app.extensions["exams"]
        event = exams.create({"term_id": self.term, "assignment_id": assignment["id"], "section_ids": [self.sections[507], self.sections[508]]})
        if date:
            event = exams.save_sitting(event["id"], {"version": event["version"], "label": title, "date": date, "time": "8 AM", "kind": "main"})
        view = self.client.get(self.base).json
        saved = self.client.post(self.base + "/setup", headers=HEADERS, json={"base_revision": view["setup"]["revision"], "request_id": str(uuid.uuid4()),
                                 "setup": {"items": [*view["setup"]["items"], {"title": title, "points": "10", "assignment_id": assignment["id"]}]}})
        self.assertEqual(saved.status_code, 200, saved.json)
        item = next(item["id"] for item in saved.json["setup"]["items"] if item["title"] == title)
        rows = self.rows()
        changes = [{"item_id": item, "row": rows[name], "base": None, **value} for name, value in scores.items()]
        written = self.client.post(self.base + "/scores", headers=HEADERS, json={"request_id": str(uuid.uuid4()), "changes": changes})
        self.assertEqual(written.status_code, 200, written.json)
        return event, item

    def test_an_iclicker_0_is_a_day_once_and_an_answered_poll_that_day_means_they_were_there(self):
        self.imported("import-1")
        # Bob answered Session 1 (Tue, Sep 1) and never joined Session 2 (Thu, Sep 3): missing there. Alice answered both.
        self.assertEqual(self.days(), [("2026-09-03", "unrecorded", ["iclicker"])])
        self.assertEqual(self.total("Example, Alice")["total"], 0)
        # A second poll of Sep 3 that Bob missed too: still one day. One he answered that day: he was there.
        self.fake.add_session(self.course_a, "Session 2b", "2026-09-03T15:30:00.000Z")
        self.imported("import-2")
        total = self.total()
        self.assertEqual((total["total"], total["not_excused"], [s["label"] for s in total["days"][0]["sources"]]),
                         (1, 1, ["iClicker 0 · Session 2", "iClicker 0 · Session 2b"]))
        late = self.fake.add_session(self.course_a, "Session 2c", "2026-09-03T15:50:00.000Z")
        self.fake.score(self.course_a, late, self.bob, 1.0, 1.0, by_enrollment=True)
        self.imported("import-3")
        self.assertEqual(self.total()["total"], 0)

    def test_an_absence_and_a_0_on_its_day_are_one_day_and_the_decision_names_it(self):
        self.imported("import-1")
        # Thu, Sep 3 to Mon, Sep 7 while the term has no class days on record: Thu, Fri and Mon count, the weekend does not.
        absence = self.record("2026-09-03", "2026-09-07", "not_excused")
        self.assertEqual(self.days(), [("2026-09-03", "not_excused", ["absence", "iclicker"]), ("2026-09-04", "not_excused", ["absence"]),
                                       ("2026-09-07", "not_excused", ["absence"])])
        # Excused: the iClicker credit fills Sep 3, so the absence alone names it; a second absence over the same day adds nothing.
        excused = self.client.post(f"/api/absences/{absence['id']}", headers=HEADERS,
                                   json={"base_revision": absence["revision"], "request_id": "excuse", "status": "excused"})
        self.assertEqual(excused.status_code, 200, excused.json)
        self.record("2026-09-04", "2026-09-04", "pending")
        total = self.total()
        self.assertEqual((total["total"], total["excused"], total["pending"], total["not_excused"]), (3, 3, 0, 0))
        self.assertEqual(self.days()[0], ("2026-09-03", "excused", ["absence"]))
        # A withdrawn absence covers nothing; one ahead of the campus day is listed, not counted.
        self.record("2026-09-08", "2026-09-08", "withdrawn")
        self.record("2026-09-24", "2026-09-24", "pending")
        total = self.total()
        self.assertEqual((total["total"], [day["date"] for day in total["ahead"]]), (3, ["2026-09-24"]))

    def test_a_quiz_0_counts_on_the_day_it_was_given_to_the_section(self):
        event, _ = self.quiz("quiz-1", "Quiz 1", "2026-09-09", {"Example, Bob": {"status": "scored", "points": "0"},
                                                                 "Example, Alice": {"status": "scored", "points": "7"}})
        self.assertEqual(self.days(), [("2026-09-09", "unrecorded", ["quiz"])])
        self.assertEqual(self.total("Example, Alice")["total"], 0)
        # A makeup sitting he missed on another day is still the quiz day, once; an absence that day is the same day.
        exams = self.app.extensions["exams"]
        makeup = exams.save_sitting(event["id"], {"version": event["version"], "label": "Makeup", "date": "2026-09-14", "time": "1 PM", "kind": "makeup"})
        sitting = next(s["id"] for s in makeup["sittings"] if s["kind"] == "makeup")
        section_id, enrollment_id = self.rows()["Example, Bob"].split(":")
        bob = next(p for p in makeup["participants"] if p["enrollment_id"] == enrollment_id)
        exams.update_participant(event["id"], section_id, enrollment_id, {"version": bob["version"], "sitting_id": sitting})
        self.record("2026-09-09", "2026-09-09", "excused")
        self.assertEqual(self.days(), [("2026-09-09", "excused", ["absence", "quiz"])])
        # Missing counts as 0 too, on its day; work with no day given on record is take-home work, never counted.
        self.quiz("quiz-2", "Quiz 2", "", {"Example, Bob": {"status": "missing"}})
        self.quiz("quiz-3", "Quiz 3", "2026-09-16", {"Example, Bob": {"status": "missing"}})
        total = self.total()
        self.assertEqual(([day["date"] for day in total["days"]], "undated" in total), (["2026-09-09", "2026-09-16"], False))

    def test_with_class_days_on_record_an_absence_counts_only_the_days_the_section_met(self):
        saved = self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": 0, "first_day": "2026-08-24", "last_day": "2026-12-03", "days_off": ["2026-09-07"],
            "entries": [{**CLASS, "section_id": self.sections[507]}, {**CLASS, "section_id": self.sections[508]}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        url = f"/api/scheduling/terms/{self.term}/use"
        used = self.client.post(url, headers=HEADERS, json={"request_id": str(uuid.uuid4()), "confirmation": "use",
                                                            "fingerprint": self.client.get(url).json["fingerprint"]})
        self.assertEqual(used.status_code, 200, used.json)
        # Mon/Wed/Fri classes, Labor Day off: only class dates count (requirement, 2026-10-31), so an exam given on Tue, Sep 8 adds
        # no day, neither for the absence over it nor for its 0.
        self.quiz("quiz-1", "Quiz 1", "2026-09-08", {"Example, Bob": {"status": "scored", "points": "0"}})
        self.record("2026-09-04", "2026-09-10", "pending")
        self.assertEqual([day[0] for day in self.days()], ["2026-09-04", "2026-09-09"])
        self.assertEqual(self.total()["pending"], 2)

    def test_the_absences_page_lists_every_students_total_by_section_most_days_first(self):
        """TOTAL ABSENCES › ON THE ABSENCES PAGE (requirement, 2026-10-31)."""
        self.imported("import-1")
        self.record("2026-09-03", "2026-09-04", "not_excused")
        totals = self.client.get("/api/absences/totals").json
        by_section = {section["section"]: section for section in totals["sections"]}
        self.assertEqual(sorted(by_section), [507, 508])
        self.assertEqual([(s["name"], s["total"]) for s in by_section[507]["students"]], [("Example, Bob", 2), ("Example, Alice", 0)])
        self.assertEqual((by_section[507]["total"], [s["name"] for s in by_section[508]["students"]]), (2, ["Example, Carol"]))
        bob = by_section[507]["students"][0]
        self.assertEqual(bob, {**self.total(), "enrollment_id": bob["enrollment_id"], "dropped": False, "name": "Example, Bob"})
        self.assertNotIn("uin", str(totals).lower())
