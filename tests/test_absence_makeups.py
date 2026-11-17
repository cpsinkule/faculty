"""Makeups owed (ABSENCES "MAKEUPS", requirement, 2026-10-03): an absence that covers the day an assignment was given (EXAMS "DATE GIVEN")
means the student owes a makeup of it until a paper of it comes back. Excused absences count on Home's Needs you; undecided ones are a
heads-up only; withdrawn and not excused ones claim nothing; a makeup sitting scheduled later neither moves the day nor clears the
reminder; only a received paper does, or the instructor's "Missed makeup, record 0" (MISSED, 2026-10-31). Nothing here writes outside
absences/."""
import csv
import datetime as dt
import io
import uuid
from unittest import mock

from test_absences import Base
from test_storage import HEADERS

from exam_workflow.given_dates import given_dates
from rosters import connect


class QuizBase(Base):
    """A term with Quiz 3 given on Sep 17 to sections 507 and 508 (Alice is in 507)."""
    def setUp(self):
        super().setUp()
        self.exams = self.app.extensions["absences"].exams
        documents = self.app.extensions["documents"]
        assignment = documents.create_assignment(self.definition, "quiz-3", "Quiz 3")
        self.event = self.exams.create({"term_id": self.term, "assignment_id": assignment["id"], "section_ids": [self.sections[507], self.sections[508]]})
        self.exams.save_sitting(self.event["id"], {"version": self.event["version"], "label": "Quiz 3", "kind": "main", "date": "2026-09-17"})

    def decide(self, status, request_id="m1", **facts):
        created = self.record(request_id=request_id, **facts)
        self.assertEqual(created.status_code, 201, created.json)
        if status == "pending":
            return created.json
        decided = self.revise(created.json, request_id + "-d", status=status)
        self.assertEqual(decided.status_code, 200, decided.json)
        return decided.json


class MakeupTests(QuizBase):
    def makeups(self):
        response = self.client.get("/api/absences/makeups")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def home(self):
        cards = self.client.get("/api/workspace/overview").json["attention"]
        return next((card for card in cards if card["key"] == "makeups"), None)

    def catalog(self):
        with connect(self.app.config["DATABASE"]) as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("exam_participants", "exam_sittings", "absence_revisions")}

    def test_an_excused_absence_over_the_day_given_owes_a_makeup_on_home_and_in_the_absence(self):
        absence = self.decide("excused")
        listed = self.makeups()
        self.assertEqual([(r["name"], r["title"], r["given_date"], r["state"], r["section"]) for r in listed["makeups"]],
                         [("Example, Alice", "Quiz 3", "2026-09-17", "owed", 507)])
        self.assertEqual(listed["counts"], {"owed": 1, "if_excused": 0, "scheduled": 0})
        card = self.home()
        self.assertEqual((card["count"], card["href"], card["label"]), (1, "#absences?view=makeups", "Makeups owed"))
        work = self.client.get(f"/api/absences/{absence['id']}").json["work"]
        self.assertEqual([(w["title"], w["state"], w["given_day"]) for w in work], [("Quiz 3", "owed", "Thu, Sep 17")])

    def test_an_undecided_absence_is_a_heads_up_and_others_claim_nothing(self):
        absence = self.decide("pending")
        self.assertEqual([r["state"] for r in self.makeups()["makeups"]], ["if_excused"])
        self.assertIsNone(self.home())  # a heads-up only: Home does not count it
        self.revise(absence, "w1", status="withdrawn")
        self.assertEqual(self.makeups()["makeups"], [])
        self.decide("not_excused", request_id="m2")
        self.assertEqual(self.makeups()["makeups"], [])
        self.decide("excused", request_id="m3", first_date="2026-09-20", last_date="2026-09-21")  # does not cover Sep 17
        self.assertEqual(self.makeups()["makeups"], [])

    def test_a_scheduled_makeup_stays_owed_and_never_moves_the_day_given(self):
        self.decide("excused")
        before = self.catalog()
        self.makeups()
        self.assertEqual(self.catalog(), before)  # reading writes nothing
        event = self.exams.get(self.event["id"])
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Makeup", "kind": "makeup", "date": "2026-09-24",
                                                      "time": "10:00", "location": "BLOC 164"})
        sitting = next(s for s in event["sittings"] if s["kind"] == "makeup")
        person = next(p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == (self.sections[507], self.alice))
        self.exams.update_participant(event["id"], self.sections[507], self.alice, {"version": person["version"], "sitting_id": sitting["id"]})
        row = self.makeups()["makeups"][0]
        self.assertEqual((row["state"], row["given_date"], row["scheduled"]["day"], row["scheduled"]["location"]),
                         ("owed", "2026-09-17", "Thu, Sep 24", "BLOC 164"))
        self.assertEqual(self.home()["count"], 1)
        with connect(self.app.config["DATABASE"]) as db:  # the Certify dialog's order still takes the student's own makeup sitting
            key = (self.sections[507], self.alice)
            self.assertEqual(given_dates(db, event["id"], self.term, event["assignment_id"], [key])[key], "2026-09-24")
            self.assertEqual(given_dates(db, event["id"], self.term, event["assignment_id"], [key], own_makeup=False)[key], "2026-09-17")

    def test_anyone_on_a_makeup_sitting_shows_without_an_absence(self):
        # SCHEDULED WITHOUT AN ABSENCE (requirement, 2026-10-19): listed until their paper comes back, never on Home's count.
        event = self.exams.get(self.event["id"])
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Replacement", "kind": "makeup",
                                                      "date": "2026-09-24", "time": "15:00", "location": "BLOC 309"})
        sitting = next(s for s in event["sittings"] if s["kind"] == "makeup")
        person = next(p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == (self.sections[507], self.alice))
        self.exams.update_participant(event["id"], self.sections[507], self.alice, {"version": person["version"], "sitting_id": sitting["id"]})
        listed = self.makeups()
        self.assertEqual([(r["name"], r["state"], r["state_label"], r["absence_id"], r["status_label"], r["given_date"], r["scheduled"]["location"])
                          for r in listed["makeups"]],
                         [("Example, Alice", "scheduled", "Makeup scheduled", "", "No absence", "2026-09-17", "BLOC 309")])
        self.assertEqual(listed["counts"], {"owed": 0, "if_excused": 0, "scheduled": 1})
        self.assertIsNone(self.home())
        self.decide("excused")  # an absence over the day claims it: one row, owed
        self.assertEqual([r["state"] for r in self.makeups()["makeups"]], ["owed"])
        real = type(self.exams)._participant_paper
        paper = lambda service, db, event, section, enrollment: {**real(service, db, event, section, enrollment), "collection": {"status": "complete"}}
        with mock.patch.object(type(self.exams), "_participant_paper", autospec=True, side_effect=paper):
            self.assertEqual(self.makeups()["makeups"], [])

    def test_only_a_received_paper_clears_it(self):
        """The Exams page's own collection of the student's paper decides (scans deleted in the Inbox are left out there)."""
        self.decide("excused")
        real = type(self.exams)._participant_paper
        for status, owed in (("no_scan", 1), ("excused", 1), ("absent", 1), ("needs_review", 0), ("missing_pages", 0), ("partial", 0), ("complete", 0)):
            paper = lambda service, db, event, section, enrollment, status=status: {**real(service, db, event, section, enrollment), "collection": {"status": status}}
            with mock.patch.object(type(self.exams), "_participant_paper", autospec=True, side_effect=paper):
                self.assertEqual(len(self.makeups()["makeups"]), owed, status)

    def test_a_dropped_student_owes_nothing(self):
        self.decide("excused")
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO roster_drops VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), self.sections[507], self.alice, "drop", "roster", "",
                                                                             "drop-1", "workspace", "2026-09-30T00:00:00Z"))
        self.assertEqual(self.makeups()["makeups"], [])
        self.assertIsNone(self.home())

    def test_scheduling_through_the_exams_routes_writes_only_the_sitting(self):
        self.decide("excused")
        event = self.client.get(f"/api/exams/{self.event['id']}").json
        made = self.client.post(f"/api/exams/{self.event['id']}/sittings", json={"version": event["version"], "kind": "makeup", "label": "Makeup",
                                                                                 "date": "2026-09-25"}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        sitting = next(s for s in made.json["sittings"] if s["kind"] == "makeup")
        person = next(p for p in made.json["participants"] if p["enrollment_id"] == self.alice and p["section_id"] == self.sections[507])
        placed = self.client.patch(f"/api/exams/{self.event['id']}/participants/{self.sections[507]}/{self.alice}",
                                   json={"version": person["version"], "sitting_id": sitting["id"]}, headers=HEADERS)
        self.assertEqual(placed.status_code, 200, placed.json)
        after = next(p for p in placed.json["participants"] if p["enrollment_id"] == self.alice and p["section_id"] == self.sections[507])
        self.assertEqual((after["expectation"], after["sitting_id"]), ("expected", sitting["id"]))
        self.assertEqual(self.makeups()["makeups"][0]["scheduled"]["sitting_id"], sitting["id"])


class MissedMakeupTests(QuizBase):
    """MAKEUPS "MISSED" (requirement, 2026-10-31): the instructor records the makeup missed; it is owed no more (Home, the Makeups view, Certify's `covering`), the
    absence window keeps it with Undo, and nothing outside absences/ is written."""

    def missed(self, missed=True, request_id="x1", **fields):
        body = {"request_id": request_id, "missed": missed, "event_id": self.event["id"], "section_id": self.sections[507],
                "enrollment_id": self.alice, **fields}
        return self.client.post("/api/absences/makeups/missed", json=body, headers=HEADERS)

    def owed(self):
        return [(r["state"], r["enrollment_id"]) for r in self.client.get("/api/absences/makeups").json["makeups"]]

    def covering(self):
        from absences import makeups
        with connect(self.app.config["DATABASE"]) as db:
            return makeups.covering(db, self.term, self.event["id"], self.event["assignment_id"], [(self.sections[507], self.alice)])

    def test_recording_it_missed_ends_the_makeup_and_undo_brings_it_back(self):
        absence = self.decide("excused")
        self.assertEqual(self.owed(), [("owed", self.alice)])
        self.assertEqual(len(self.covering()), 1)
        with connect(self.app.config["DATABASE"]) as db:
            before = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("exam_participants", "absence_revisions", "gradebook_scores")}
        made = self.missed()
        self.assertEqual(made.status_code, 200, made.json)
        self.assertEqual((made.json["missed"], made.json["revision"]), (True, 1))
        self.assertEqual(self.owed(), [])
        self.assertIsNone(next((c for c in self.client.get("/api/workspace/overview").json["attention"] if c["key"] == "makeups"), None))
        self.assertEqual(self.covering(), {})  # Certify gives the 0
        work = self.client.get(f"/api/absences/{absence['id']}").json["work"]
        self.assertEqual([(w["title"], w["state"], w["state_label"]) for w in work], [("Quiz 3", "missed", "Missed makeup: 0")])
        self.assertEqual(self.missed().json["revision"], 1)  # the same request again records nothing
        self.assertEqual(self.missed(request_id="x2").json["revision"], 1)  # already missed: nothing new
        self.assertEqual(self.missed(False, request_id="x1").status_code, 409)  # a request ID names one change
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual({t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}, before)
            self.assertEqual(db.execute("SELECT absence_id, actor FROM absence_makeups_missed").fetchone()[:], (absence["id"], "workspace"))
        undone = self.missed(False, request_id="u1")
        self.assertEqual((undone.json["missed"], undone.json["revision"]), (False, 2))
        self.assertEqual(self.owed(), [("owed", self.alice)])
        self.assertEqual(len(self.covering()), 1)

    def test_only_a_makeup_owed_now_can_be_missed(self):
        self.assertEqual(self.missed().status_code, 409)  # no absence, no makeup sitting
        self.decide("pending")
        self.assertEqual(self.missed().status_code, 409)  # an undecided absence's row offers none in the page; the route agrees
        self.assertEqual(self.missed(request_id="").status_code, 400)
        self.assertEqual(self.missed(missed="yes").status_code, 400)
        self.assertEqual(self.missed(event_id=str(uuid.uuid4())).status_code, 404)

    def test_a_makeup_sitting_without_an_absence_can_be_missed(self):
        event = self.exams.get(self.event["id"])
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Replacement", "kind": "makeup", "date": "2026-09-24"})
        sitting = next(s for s in event["sittings"] if s["kind"] == "makeup")
        person = next(p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == (self.sections[507], self.alice))
        self.exams.update_participant(event["id"], self.sections[507], self.alice, {"version": person["version"], "sitting_id": sitting["id"]})
        self.assertEqual(self.owed(), [("scheduled", self.alice)])
        self.assertEqual(self.missed().status_code, 200)
        self.assertEqual(self.owed(), [])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertIsNone(db.execute("SELECT absence_id FROM absence_makeups_missed").fetchone()[0])


class GradebookZeroOnceDueTests(QuizBase):
    """GRADEBOOK "ZERO ONCE DUE" (requirement, 2026-10-06): an empty cell counts 0 the day after its column's due date (campus
    clock), or once a column linked to grading is certified, unless the student owes a makeup of it (or an undecided absence would
    owe one), has a DR booking for it, or has a paper not graded yet. A read: nothing is written."""

    def setUp(self):
        super().setUp()
        self.ids = self.setup_gradebook([{"title": "Quiz 3", "points": "10", "assignment_id": self.event["assignment_id"], "due_date": "2026-09-17"},
                                         {"title": "HW 1", "points": "10", "due_date": "2026-09-30"}])
        self.row = f"{self.sections[507]}:{self.alice}"

    def on(self, today):
        return mock.patch("scheduling.clock.campus_today", return_value=dt.date.fromisoformat(today))

    def view(self, today):
        with self.on(today):
            response = self.client.get(f"/api/gradebook/terms/{self.term}")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def blanks(self, today):
        found = self.view(today)["results"]["blanks"].get(self.row, {})
        return {title: found.get(ident) for title, ident in self.ids.items()}

    def test_a_new_term_counts_empty_cells_once_their_due_date_has_passed(self):
        view = self.view("2026-09-17")
        self.assertEqual(view["setup"]["ungraded"], "due")
        self.assertEqual((view["results"]["blanks"], view["results"]["students"][self.row]["percent"]), ({}, None))  # due today: not yet
        self.assertEqual(self.blanks("2026-09-30"), {"Quiz 3": "zero", "HW 1": None})
        self.assertEqual(self.view("2026-09-30")["results"]["students"][self.row]["percent"], "0.00")
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "zero", "HW 1": "zero"})
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_scores").fetchone()[0], 0)  # counted, never written

    def test_a_makeup_owed_or_an_undecided_absence_keeps_the_blank_out_until_decided(self):
        absence = self.decide("pending")
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "if_excused", "HW 1": "zero"})  # HW 1 has no exam record: no makeup
        excused = self.revise(absence, "d1", status="excused")
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "makeup", "HW 1": "zero"})
        self.assertEqual(self.view("2026-10-01")["results"]["students"][self.row]["possible"], "10")
        self.revise(excused.json, "d2", status="not_excused")
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "zero", "HW 1": "zero"})

    def test_a_makeup_recorded_missed_counts_its_blank_as_0(self):
        self.decide("excused")
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "makeup", "HW 1": "zero"})
        made = self.client.post("/api/absences/makeups/missed", json={"request_id": "x1", "missed": True, "event_id": self.event["id"],
                                "section_id": self.sections[507], "enrollment_id": self.alice}, headers=HEADERS)
        self.assertEqual(made.status_code, 200, made.json)
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "zero", "HW 1": "zero"})

    def test_a_received_paper_not_graded_yet_and_a_dr_booking_wait(self):
        real = type(self.exams)._participant_paper
        paper = lambda service, db, event, section, enrollment: {**real(service, db, event, section, enrollment), "collection": {"status": "complete"}}
        with mock.patch.object(type(self.exams), "_participant_paper", autospec=True, side_effect=paper):
            self.assertEqual(self.blanks("2026-10-01")["Quiz 3"], "paper")
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            snapshot = '{"date": "2026-09-18", "start_time": "10:00", "end_time": "11:00", "exam_type": "Quiz 3"}'
            db.execute("INSERT INTO dr_exam_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                       ("rev-1", self.sections[507], self.alice, "quiz 3", "job-1", 1, snapshot, "0" * 64, "2026-09-10T00:00:00Z"))
            db.execute("INSERT INTO dr_exam_bookings VALUES(?,?,?,?,?)", ("booking-1", self.sections[507], self.alice, "quiz 3", "rev-1"))
            db.execute("INSERT INTO dr_exam_assignment_links VALUES(?,?,?,?,?)",
                       ("booking-1", self.event["assignment_id"], "manual", "2026-09-10T00:00:00Z", "2026-09-10T00:00:00Z"))
        self.assertEqual(self.blanks("2026-10-01"), {"Quiz 3": "dr", "HW 1": "zero"})

    def test_a_column_linked_to_grading_counts_once_certified(self):
        setup = self.view("2026-10-01")["setup"]
        items = [{**item, "link": {"kind": "grading", "parts": ["exams"]}} if item["title"] == "Quiz 3" else item for item in setup["items"]]
        saved = self.client.post(f"/api/gradebook/terms/{self.term}/setup", json={"base_revision": setup["revision"], "request_id": "link",
                                                                                     "setup": {"items": items}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(self.blanks("2026-10-01")["Quiz 3"], None)  # past its date, but its grading is not certified
        certified = {"id": "cert-1", "created_at": "2026-09-25T00:00:00Z", "summary_json": "{}", "parts_json": "[]"}
        with mock.patch("gradebook.sources.latest_certification", return_value=certified):
            self.assertEqual(self.blanks("2026-10-01")["Quiz 3"], "zero")

    def test_a_save_and_the_export_count_what_the_grid_counts(self):
        with self.on("2026-10-01"):
            written = self.client.post(f"/api/gradebook/terms/{self.term}/scores", json={"request_id": "s1", "changes": [
                {"item_id": self.ids["HW 1"], "row": self.row, "base": None, "status": "scored", "points": "10"}]}, headers=HEADERS)
            self.assertEqual(written.status_code, 200, written.json)
            self.assertEqual(written.json["results"]["students"][self.row]["percent"], "50.00")  # Quiz 3 counts 0 of 10
            export = self.client.get(f"/api/gradebook/terms/{self.term}/export.csv").get_data(as_text=True)
        rows = list(csv.DictReader(io.StringIO(export.lstrip("\ufeff"))))
        alice = next(row for row in rows if row["Name"] == "Example, Alice" and row["Section"] == "507")
        self.assertEqual((alice["Quiz 3 (10)"], alice["Overall %"]), ("", "50.00"))  # the cell stays empty; the total counts it
        self.assertEqual(self.view("2026-10-01")["results"]["students"][self.row]["percent"], "50.00")
