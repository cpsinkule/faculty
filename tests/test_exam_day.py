"""Exam-day packets (AGENTS.md "Exam-day packet"): a term-level plan per assessment (proctor instructions and each room's proctors)
kept as append-only revisions with request IDs and stale checks, the TAs proctors are picked from, and one room's packet: the room
roster's own read, the copies of each version from the chart's letters, the extras of its current print jobs, and one fingerprint."""
from datetime import datetime
import io
import json
import sqlite3
import unittest

from exam_day.service import DEFAULT_INSTRUCTIONS
from rosters import connect
import test_seating as fixture
from test_dr_exams import booking
from test_rosters import csv_bytes

HEADERS = fixture.HEADERS


class ExamDayTests(unittest.TestCase):
    import_dr = fixture.SeatingTests.import_dr
    create = fixture.SeatingTests.create

    def setUp(self):
        fixture.SeatingTests.setUp(self)
        self.chart = self.create().json
        self.path = self.app.extensions["rosters"].path
        self.term_id = self.chart["term_id"]
        self.route = f"/api/exam-day/{self.assignment['id']}/{self.term_id}"
        self.packet_route = f"/api/exam-day/charts/{self.chart['id']}/packet"

    def plan(self, route=None):
        response = self.client.get(route or self.route)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def save(self, route=None, **body):
        return self.client.post(route or self.route, json={"base_revision": 0, "request_id": "r1", "instructions": "Arrive early.",
                                                           "proctors": {}, **body}, headers=HEADERS)

    def packet(self):
        response = self.client.get(self.packet_route)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def rows(self):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute("SELECT * FROM exam_day_plans ORDER BY revision")]

    def confirm(self, request_id, extras=2):
        chart = self.client.get(f"/api/seating/{self.chart['id']}").json
        response = self.client.post(f"/api/seating/{self.chart['id']}/confirm", json={
            "version": chart["version"], "mode": "stamps_only", "page_count": 2, "request_id": request_id, "extras": extras,
            "prepare_cards": False}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["exported_batches"]

    def set_tas(self, number, contacts):
        section = self.sections[number]["id"]
        response = self.client.post(f"/api/teaching-assistants/sections/{section}",
                                    json={"contacts": contacts, "base_revision": 0, "request_id": f"tas-{number}"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)

    def test_the_assistants_save_is_stored_as_the_assistants(self):
        """requirement, 2026-11-06 (assistant.md: records that name an actor say the assistant made them, request_actor.py)."""
        self.assertEqual(self.save().status_code, 200)
        theirs = self.client.post(self.route, json={"base_revision": 1, "request_id": "r2", "instructions": "Arrive early, bring a pencil.", "proctors": {}},
                                  headers=HEADERS, environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(theirs.status_code, 200, theirs.json)
        self.assertEqual([(row["request_id"], row["actor"]) for row in self.rows()], [("r1", "workspace"), ("r2", "assistant")])

    def test_a_new_plan_shows_the_default_instructions_and_each_room_without_uins(self):
        view = self.plan()
        self.assertEqual((view["revision"], view["saved_at"], view["proctors"]), (0, None, {}))
        self.assertEqual(view["instructions"], DEFAULT_INSTRUCTIONS)
        self.assertEqual(view["default_instructions"], DEFAULT_INSTRUCTIONS)
        self.assertEqual((view["assignment"]["title"], view["term"]["term_label"]), ("Test 1", "Fall 2026"))
        self.assertEqual(view["exam_href"], f"#exams/assessment/{self.assignment['id']}/{self.term_id}")
        [room] = view["rooms"]
        self.assertEqual((room["chart_id"], room["room_name"], room["sections"], room["confirmed"]), (self.chart["id"], "HECC 105", [507, 508], False))
        self.assertEqual((room["seated"], room["dr_count"], room["copies"], room["extras"], room["printing"]), (7, 2, None, None, None))
        self.assertEqual((room["version_count"], room["version_totals"], room["unassigned_versions"]), (None, {}, 0))
        encoded = json.dumps(view)
        for student in self.chart["students"]:
            self.assertNotIn(student["uin"], encoded)
            self.assertNotIn(student["enrollment_id"], encoded)
        # Every default step the instructor asked for is there, and it is plain text the packet prints as written.
        for words in ("early", "photo ID against the room roster", "seat the chart gives", "version the seating chart marks",
                      "including the unused extras", "DR center", "contact the instructor"):
            self.assertIn(words, DEFAULT_INSTRUCTIONS)

    def test_saves_are_revisions_with_stale_checks_request_ids_and_nothing_for_an_identical_plan(self):
        proctors = {self.chart["id"]: [{"name": "  Pat   Proctor ", "email": "pat@tamu.edu", "phone": "979-555-0100"}]}
        first = self.save(proctors=proctors, instructions="Arrive early.  \r\nCount the copies.\n\n")
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual((first.json["revision"], first.json["instructions"]), (1, "Arrive early.\nCount the copies."))
        self.assertEqual(first.json["proctors"], {self.chart["id"]: [{"name": "Pat Proctor", "email": "pat@tamu.edu", "phone": "979-555-0100"}]})
        self.assertEqual(first.json["rooms"][0]["proctors"], first.json["proctors"][self.chart["id"]])
        # The same request again returns what it did; the same ID with another plan is refused.
        again = self.save(proctors=proctors, instructions="Arrive early.\nCount the copies.")
        self.assertEqual((again.status_code, again.json["revision"]), (200, 1))
        other = self.save(proctors={}, instructions="Something else")
        self.assertEqual(other.status_code, 409)
        self.assertIn("different plan", other.json["error"])
        # A save made on an older revision is refused; an identical plan records nothing.
        stale = self.save(request_id="r2", instructions="Late edit")
        self.assertEqual(stale.status_code, 409)
        self.assertIn("changed elsewhere", stale.json["error"])
        same = self.save(request_id="r3", base_revision=1, proctors=proctors, instructions="Arrive early.\nCount the copies.")
        self.assertEqual((same.status_code, same.json["revision"]), (200, 1))
        changed = self.save(request_id="r4", base_revision=1, proctors={}, instructions="Arrive early.")
        self.assertEqual((changed.status_code, changed.json["revision"], changed.json["proctors"]), (200, 2, {}))
        self.assertEqual([(row["revision"], row["request_id"]) for row in self.rows()], [(1, "r1"), (2, "r4")])
        with connect(self.path) as db:
            for statement in ("UPDATE exam_day_plans SET instructions='x'", "DELETE FROM exam_day_plans"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                    db.execute(statement)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("INSERT INTO exam_day_plans VALUES('x',?,?,5,'','{}','skip','workspace','now')", (self.term_id, self.assignment["id"]))

    def test_plans_and_proctors_are_checked(self):
        other = self.create(assignment_id=self.quiz["id"], section_ids=[self.sections[508]["id"]], room_id=self.small["id"]).json
        person = {"name": "Pat Proctor", "email": "pat@tamu.edu"}
        for body, status, message in (
            ({"proctors": {self.chart["id"]: [{"email": "pat@tamu.edu"}]}}, 400, "Give proctor 1 a name"),
            ({"proctors": {self.chart["id"]: [{"name": "Pat", "email": "pat at tamu"}]}}, 400, "does not look like one"),
            ({"proctors": {self.chart["id"]: [{"name": "Pat", "phone": "call me"}]}}, 400, "phone number"),
            ({"proctors": {self.chart["id"]: [person, {**person, "name": "Pat again"}]}}, 400, "listed twice"),
            ({"proctors": {self.chart["id"]: [{"name": f"P{index}"} for index in range(13)]}}, 400, "at most 12"),
            ({"proctors": {self.chart["id"]: person}}, 400, "as a list"),
            ({"proctors": [person]}, 400, "object of rooms"),
            ({"proctors": {other["id"]: [person]}}, 400, "another exam"),
            ({"proctors": {"no-such-chart": [person]}}, 409, "no longer has a seating chart"),
            ({"instructions": "x" * 8001}, 400, "longer than 8,000"),
            ({"instructions": "bell\x07"}, 400, "cannot be printed"),
            ({"instructions": None}, 400, "as text"),
            ({"request_id": "no spaces allowed"}, 400, "request ID"),
            ({"base_revision": -1}, 400, "base_revision"),
        ):
            with self.subTest(body=body):
                response = self.save(**body)
                self.assertEqual(response.status_code, status, response.json)
                self.assertIn(message, response.json["error"])
        self.assertEqual(self.rows(), [])
        # A plan of the other exam is its own; a room with no proctors is simply left out.
        ok = self.save(route=f"/api/exam-day/{self.quiz['id']}/{self.term_id}", proctors={other["id"]: [person]})
        self.assertEqual(ok.status_code, 200, ok.json)
        self.assertEqual(self.plan()["revision"], 0)
        empty_room = self.save(request_id="r9", proctors={self.chart["id"]: []})
        self.assertEqual(empty_room.json["proctors"], {})
        # A term of another course, an unknown assignment or an unknown term is not a plan.
        course = self.client.post("/api/courses", json={"name": "Algebra", "code": "MATH 142", "term": "Fall", "year": 2026, "sections": [501]},
                                  headers=HEADERS)
        self.assertEqual(course.status_code, 201, course.json)
        foreign = next(item["term_id"] for item in self.client.get("/api/seating/catalog").json["sections"] if item["code"] == "MATH 142")
        for route in (f"/api/exam-day/{self.assignment['id']}/{foreign}", f"/api/exam-day/nope/{self.term_id}", f"/api/exam-day/{self.assignment['id']}/nope"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 404)
                self.assertEqual(self.save(route=route, request_id="r10").status_code, 404)

    def test_proctors_are_offered_from_the_rooms_sections_first_then_the_terms_other_sections(self):
        self.set_tas(507, [{"name": "Ada TA", "email": "ada@tamu.edu", "phone": "979-555-0101"}, {"name": "Shared TA", "email": "shared@tamu.edu"}])
        self.set_tas(508, [{"name": "Bea TA", "email": "bea@tamu.edu"}, {"name": "Shared TA", "email": "SHARED@tamu.edu"}])
        [room] = self.plan()["rooms"]
        self.assertEqual([(item["name"], item["sections"], item["own"]) for item in room["ta_options"]],
                         [("Ada TA", [507], True), ("Bea TA", [508], True), ("Shared TA", [507, 508], True)])
        # One room per section: the other section's TAs are still offered, marked as the term's other sections.
        result = self.client.delete(f"/api/seating/{self.chart['id']}", json={"version": self.chart["version"]}, headers=HEADERS)
        self.assertEqual(result.status_code, 204)
        first = self.create(section_ids=[self.sections[507]["id"]]).json
        self.create(section_ids=[self.sections[508]["id"]], room_id=self.small["id"])
        rooms = {room["chart_id"]: room for room in self.plan()["rooms"]}
        self.assertEqual([(item["name"], item["sections"], item["own"]) for item in rooms[first["id"]]["ta_options"]],
                         [("Ada TA", [507], True), ("Shared TA", [507, 508], True), ("Bea TA", [508], False)])
        self.assertEqual(rooms[first["id"]]["ta_options"][0]["phone"], "979-555-0101")

    def test_start_from_offers_the_courses_other_plans_in_any_term(self):
        quiz_route = f"/api/exam-day/{self.quiz['id']}/{self.term_id}"
        self.assertEqual(self.plan()["others"], [])
        self.assertEqual(self.save(route=quiz_route, instructions="Quiz rules.").status_code, 200)
        self.assertEqual(self.save(request_id="own", instructions="Test rules.").status_code, 200)
        others = self.plan()["others"]
        self.assertEqual([(item["assignment_id"], item["title"], item["term_label"], item["instructions"]) for item in others],
                         [(self.quiz["id"], "Quiz 3", "Fall 2026", "Quiz rules.")])
        # A plan whose instructions were emptied is not offered to start from.
        self.assertEqual(self.save(route=quiz_route, request_id="blank", base_revision=1, instructions="").status_code, 200)
        self.assertEqual(self.plan()["others"], [])

    def test_the_packet_holds_the_room_roster_versions_extras_plan_and_one_fingerprint(self):
        packet = self.packet()
        roster = self.client.get(f"/api/seating/{self.chart['id']}/room-roster").json
        self.assertEqual(packet["roster"], roster)
        self.assertEqual((packet["seated"], packet["dr_count"], packet["room_name"], packet["sections"]), (7, 2, "HECC 105", [507, 508]))
        self.assertEqual((packet["instructions"], packet["default_instructions"], packet["plan_revision"], packet["proctors"]),
                         (DEFAULT_INSTRUCTIONS, True, 0, []))
        self.assertIsNone(packet["versions"])
        self.assertIsNone(packet["printing"])
        self.assertTrue(any("No print jobs" in warning for warning in packet["warnings"]))
        self.assertEqual(packet["plan_href"], f"#exam-day/{self.assignment['id']}/{self.term_id}")
        # UINs only in the room roster, exactly as Print room roster shows them.
        outside = json.dumps({key: value for key, value in packet.items() if key != "roster"})
        for student in self.chart["students"]:
            self.assertNotIn(student["uin"], outside)
            self.assertNotIn(student["enrollment_id"], json.dumps(packet))
        # Versions: the chart's saved letters.
        chart = self.client.get(f"/api/seating/{self.chart['id']}").json
        assigned = self.client.post(f"/api/seating/{self.chart['id']}/exam-versions", json={"version": chart["version"], "count": 2}, headers=HEADERS)
        self.assertEqual(assigned.status_code, 200, assigned.json)
        versioned = self.packet()
        self.assertEqual(versioned["versions"]["counts"], [{"version": "A", "count": 4}, {"version": "B", "count": 3}])
        self.assertEqual((versioned["versions"]["count"], versioned["versions"]["unassigned"]), (2, 0))
        self.assertNotEqual(versioned["fingerprint"], packet["fingerprint"])
        self.assertEqual(self.plan()["rooms"][0]["version_totals"], {"A": 4, "B": 3})
        # Extras and copies: the current print jobs, per section.
        first = self.confirm("export-1", extras=2)
        printed = self.packet()["printing"]
        self.assertEqual([(item["section_number"], item["copies"], item["extras"], item["status"]) for item in printed["sections"]],
                         [(507, 7, 2, "pending"), (508, 4, 2, "pending")])
        self.assertEqual((printed["copies"], printed["extras"], printed["ready"], printed["duplicates"]), (11, 4, False, []))
        room = self.plan()["rooms"][0]
        self.assertEqual((room["copies"], room["extras"], room["confirmed"]), (11, 4, True))
        # Exported again: the latest job of each section counts, and the earlier current ones are named, never added up.
        second = self.confirm("export-2", extras=1)
        printed = self.packet()["printing"]
        self.assertEqual((printed["copies"], printed["extras"]), (9, 2))
        self.assertEqual(printed["duplicates"], [{"section_number": 507, "jobs": 2}, {"section_number": 508, "jobs": 2}])
        self.assertTrue(any("2 current print jobs" in warning for warning in self.packet()["warnings"]))
        # Cancelled and superseded jobs never count.
        for item in second:
            self.assertEqual(self.client.post(f"/api/documents/batches/{item['id']}/cancel", json={}, headers=HEADERS).status_code, 200)
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET superseded_at='2026-09-30T00:00:00Z' WHERE id=?", (first[1]["id"],))
        printed = self.packet()["printing"]
        self.assertEqual([(item["section_number"], item["copies"], item["extras"]) for item in printed["sections"]], [(507, 7, 2)])
        self.assertEqual(printed["duplicates"], [])
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET superseded_at='2026-09-30T00:00:00Z' WHERE id=?", (first[0]["id"],))
        self.assertIsNone(self.packet()["printing"])

    def test_the_fingerprint_follows_the_plan_the_roster_and_the_dr_schedule(self):
        before = self.packet()
        self.assertEqual(self.packet()["fingerprint"], before["fingerprint"])
        saved = self.save(proctors={self.chart["id"]: [{"name": "Pat Proctor", "phone": "979-555-0100"}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        planned = self.packet()
        self.assertNotEqual(planned["fingerprint"], before["fingerprint"])
        self.assertEqual((planned["instructions"], planned["default_instructions"], planned["plan_revision"]), ("Arrive early.", False, 1))
        self.assertEqual(planned["proctors"], [{"name": "Pat Proctor", "email": "", "phone": "979-555-0100"}])
        self.import_dr([booking(Date=datetime(2026, 9, 20), **{"Start Time": "09:30 AM", "End Time": "11:23 AM"})])
        rescheduled = self.packet()
        self.assertNotEqual(rescheduled["fingerprint"], planned["fingerprint"])
        self.assertEqual(rescheduled["roster"], self.client.get(f"/api/seating/{self.chart['id']}/room-roster").json)
        # A proctor saved for another exam's room never reaches this one.
        other = self.create(assignment_id=self.quiz["id"], section_ids=[self.sections[508]["id"]], room_id=self.small["id"]).json
        self.save(route=f"/api/exam-day/{self.quiz['id']}/{self.term_id}", proctors={other["id"]: [{"name": "Quinn"}]})
        self.assertEqual(self.packet()["fingerprint"], rescheduled["fingerprint"])

    def test_the_packet_refuses_what_print_room_roster_refuses(self):
        rows = [*fixture.SECTION_507, fixture.student_row("New, Student", "444444444")]
        result = self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster", data={
            "file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertEqual(result.status_code, 201)
        refused = self.client.get(self.packet_route)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Update seating", refused.json["error"])
        self.assertEqual(self.client.get("/api/exam-day/charts/no-such-chart/packet").status_code, 404)
        # The plan page still reads: it names the room's check, it does not print anything.
        self.assertFalse(self.plan()["rooms"][0]["check_ok"])


if __name__ == "__main__":
    unittest.main()
