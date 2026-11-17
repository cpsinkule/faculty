"""Exam seating charts: DR exclusion, capacity errors, update/regenerate, moves, and ownership."""
import io
import json
import re
import tempfile
import unittest
import uuid
from unittest.mock import patch

from app import create_app
from rosters import connect
from seating import recorded_time
from test_dr_exams import booking, workbook as dr_workbook
from test_rooms import FIXTURES, chart_bytes, fixture
from test_rosters import COLUMNS, csv_bytes

# HECC 105 seats 75 spaced; the invented hall that stands in for it in the public copy seats 88.
HALL_SPACED = 75 if (FIXTURES / "HECC 105.xlsx").exists() else 88

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


def student_row(name, uin):
    row = [""] * len(COLUMNS)
    row[1], row[2] = name, uin
    row[COLUMNS.index("Email")] = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".") + "@example.test"
    return row


SECTION_507 = [student_row("Example, Alice", "001234567"), student_row("Example, Bob", "123456789"),
               *[student_row(f"Student, {index}", f"00000000{index}") for index in range(3, 7)]]
SECTION_508 = [student_row("Example, Carol", "222222222"), student_row("Student, 8", "000000008"), student_row("Student, 9", "000000009")]


class SeatingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.inbox = self.app.extensions["inbox"]
        response = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                          "sections": [507, 508], "multiple_sections": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        state = self.client.get("/api/state").json
        self.group = state["course_groups"][0]
        self.definition_id = state["course_definitions"][0]["id"]
        for number, rows in ((507, SECTION_507), (508, SECTION_508)):
            result = self.client.post(f"/api/course-groups/{self.group['id']}/sections/{number}/roster",
                                      data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            self.assertEqual(result.status_code, 201, result.json)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        self.quiz = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "quiz-3", "title": "Quiz 3"}, headers=HEADERS).json
        self.hall = self.client.post("/api/rooms", data={"name": "HECC 105", "file": (io.BytesIO(fixture("HECC 105")), "HECC 105.xlsx")}, headers=HEADERS).json
        self.small = self.client.post("/api/rooms", data={"name": "Tiny", "file": (io.BytesIO(chart_bytes({"A1": "A1", "B1": "A2", "C1": "A3", "A2": "B1"})), "tiny.xlsx")}, headers=HEADERS).json
        seats = {seat["label"]: seat for seat in self.small["seats"]}
        self.small = self.client.post(f"/api/rooms/{self.small['id']}/seats", json={"version": self.small["version"], "seat_ids": [seats["B1"]["id"]], "broken": True}, headers=HEADERS).json
        self.sections = {item["number"]: item for item in self.client.get("/api/seating/catalog").json["sections"]}
        self.import_dr([booking(), booking(**{"Exam Type": "Final"}), booking(UIN="222222222", Section="508", **{"Last Name": "Example", "First Name": "Carol"})])

    def import_dr(self, rows):
        content = dr_workbook(rows)
        preview = self.client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None)}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        submitted = self.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None),
                                     "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(submitted.status_code, 202, submitted.json)
        self.assertTrue(self.inbox.process_one())
        job = self.inbox.get(submitted.json["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))

    def create(self, **overrides):
        payload = {"assignment_id": self.assignment["id"], "section_ids": [self.sections[507]["id"], self.sections[508]["id"]],
                   "room_id": self.hall["id"], **overrides}
        return self.client.post("/api/seating", json=payload, headers=HEADERS)

    def by_name(self, chart):
        return {student["name"]: student for student in chart["students"]}

    def read_card_by_card(self, route):
        """The chart as reading each card's mail and submission on its own gives it (`_card_states` reads them for all cards at once)."""
        seating = self.app.extensions["seating"]
        per_card = seating._card_state
        with patch.object(seating, "_card_state", lambda db, kind, card, entry, recorded_seat, facts=None: per_card(db, kind, card, entry, recorded_seat)):
            return self.client.get(route).json

    def test_the_catalog_lists_rooms_for_the_seating_forms(self):
        # The New chart dialog and the assignment wizard take their rooms from here (only the bubble sheet panel leaves them out).
        keys = ("id", "name", "seat_count", "usable_count", "broken_count", "blocked_count", "spaced_count", "review_required")
        rooms = self.client.get("/api/seating/catalog").json["rooms"]
        self.assertEqual(rooms, [{key: room[key] for key in keys} for room in (self.hall, self.small)])
        self.assertEqual({key: rooms[1][key] for key in keys[2:]},
                         {"seat_count": 4, "usable_count": 3, "broken_count": 1, "blocked_count": 0, "spaced_count": 2, "review_required": []})

    def test_room_source_review_blocks_generation_moves_and_printing(self):
        service = self.app.extensions["rooms"]
        content = chart_bytes({"A1": "G", **{f"{chr(66 + index)}1": max(1, index) for index in range(10)},
                               "A2": "F", "B2": 1})
        room = service.create("Review room", "", content, "review.xlsx", allow_ambiguous=True)
        self.assertTrue(room["review_required"])
        failed = self.create(room_id=room["id"])
        self.assertEqual(failed.status_code, 409, failed.json)
        self.assertIn("needs a room review", failed.json["error"])
        self.assertEqual(self.client.get("/api/seating").json, [])
        ready = self.client.patch(f"/api/rooms/{room['id']}", json={"version": room["version"], "resolve_review": True}, headers=HEADERS)
        self.assertEqual(ready.status_code, 200, ready.json)
        chart = self.create(room_id=room["id"]).json
        old_seats = {seat["id"] for seat in ready.json["seats"]}
        changed = service.replace_chart(room["id"], ready.json["version"], content, "review.xlsx", allow_ambiguous=True)
        self.assertEqual(old_seats, {seat["id"] for seat in changed["seats"]})
        live = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertFalse(live["check"]["ok"])
        self.assertTrue(live["check"]["counts"]["room_review"])
        self.assertTrue(any("Room review" in text for text in live["issues"]))
        student = next(item for item in live["students"] if item["seat_id"])
        for action, extra in (("update", {}), ("regenerate", {}),
                              ("move", {"enrollment_id": student["enrollment_id"], "seat_id": student["seat_id"]}),
                              ("confirm", {"mode": "stamps_only", "page_count": 1, "request_id": "review-print", "acknowledge_issues": True})):
            result = self.client.post(f"/api/seating/{chart['id']}/{action}", json={"version": live["version"], **extra}, headers=HEADERS)
            self.assertEqual(result.status_code, 409, (action, result.json))
            self.assertIn("needs a room review", result.json["error"])

    def test_options_report_students_and_linked_dr_bookings(self):
        options = self.client.get(f"/api/seating/options?assignment_id={self.assignment['id']}&sections={self.sections[507]['id']},{self.sections[508]['id']}").json
        self.assertEqual((options["students"], options["assignment_title"], options["sections"]), (9, "Test 1", [507, 508]))
        self.assertEqual(options["dr"]["excluded_students"], 2)
        # Bob's Final booking created a "Final" assignment automatically and is linked there.
        self.assertEqual([(item["title"], item["key"], item["students"], item["bookings"], item["auto_created"]) for item in options["dr"]["others"]],
                         [("Final", "final", 1, 1, True)])
        only = self.client.get(f"/api/seating/options?assignment_id={self.quiz['id']}&sections={self.sections[508]['id']}").json
        self.assertEqual((only["students"], only["dr"]["excluded_students"], [item["title"] for item in only["dr"]["others"]]), (3, 0, ["Test 1"]))
        self.assertEqual(self.client.get("/api/seating/options?assignment_id=nope&sections=x").status_code, 400)

    def test_validation_before_any_chart_is_created(self):
        cases = (
            ({"relink_assignment_ids": "x"}, "which other exams"),
            ({"relink_assignment_ids": ["missing"]}, "retained exam assignments"),
            ({"section_ids": []}, "at least one section"),
            ({"section_ids": ["missing"]}, "no longer exists"),
            ({"assignment_id": "missing"}, "retained exam assignment"),
            ({"room_id": "missing"}, "Choose a room"),
            ({"notes": "x" * 281}, "280 characters"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                response = self.create(**overrides)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn(message, response.json["error"])
        self.client.post("/api/courses", json={"name": "Physics", "code": "PHYS 206", "term": "Fall", "year": 2026, "sections": [1]}, headers=HEADERS)
        physics = next(item for item in self.client.get("/api/seating/catalog").json["sections"] if item["code"] == "PHYS 206")
        mixed = self.create(section_ids=[self.sections[507]["id"], physics["id"]])
        self.assertIn("one course and term", mixed.json["error"])
        wrong = self.create(section_ids=[physics["id"]])
        self.assertIn("assignment belongs to MATH 140", wrong.json["error"])
        self.assertEqual(self.client.get("/api/seating").json, [])
        self.assertEqual(self.client.post("/api/seating", data={"x": "1"}, headers=HEADERS).status_code, 415)

    def test_plans_seat_sections_in_different_rooms_together_or_not_at_all(self):
        options = self.client.get(f"/api/seating/options?assignment_id={self.assignment['id']}&sections={self.sections[507]['id']},{self.sections[508]['id']}").json
        self.assertEqual(options["per_section"], {self.sections[507]["id"]: {"number": 507, "students": 6, "excluded": 1},
                                                  self.sections[508]["id"]: {"number": 508, "students": 3, "excluded": 1}})
        # Section 507 (5 to seat) cannot fit the 3-seat room, so neither chart is created.
        failed = self.client.post("/api/seating/plan", json={"assignment_id": self.assignment["id"], "allocations": [
            {"room_id": self.hall["id"], "section_ids": [self.sections[508]["id"]]}, {"room_id": self.small["id"], "section_ids": [self.sections[507]["id"]]}]}, headers=HEADERS)
        self.assertEqual(failed.status_code, 409, failed.json)
        self.assertIn("5 students need seats but Tiny has only 3 usable seats free", failed.json["error"])
        self.assertEqual(self.client.get("/api/seating").json, [])
        for payload, message in (
            ({"allocations": []}, "at least one section"),
            ({"allocations": [{"room_id": self.hall["id"], "section_ids": [self.sections[507]["id"]]}, {"room_id": self.small["id"], "section_ids": [self.sections[507]["id"]]}]}, "one room"),
            ({"allocations": [{"room_id": self.hall["id"], "section_ids": [self.sections[507]["id"]]}, {"room_id": self.hall["id"], "section_ids": [self.sections[508]["id"]]}]}, "each room once"),
            ({"allocations": [{"room_id": self.hall["id"], "section_ids": []}]}, "at least one section"),
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/api/seating/plan", json={"assignment_id": self.assignment["id"], **payload}, headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn(message, response.json["error"])
        created = self.client.post("/api/seating/plan", json={"assignment_id": self.assignment["id"], "notes": "Two rooms", "allocations": [
            {"room_id": self.hall["id"], "section_ids": [self.sections[507]["id"]]}, {"room_id": self.small["id"], "section_ids": [self.sections[508]["id"]]}]}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        charts = created.json["charts"]
        self.assertEqual([(chart["room_name"], chart["sections"], chart["counts"]["seated"], chart["counts"]["excluded_dr"], chart["notes"]) for chart in charts],
                         [("HECC 105", [507], 5, 1, "Two rooms"), ("Tiny", [508], 2, 1, "Two rooms")])
        listing = self.client.get("/api/seating").json
        self.assertEqual({(item["room_name"], tuple(item["sections"]), tuple(item["term_sections"])) for item in listing}, {("HECC 105", (507,), (507, 508)), ("Tiny", (508,), (507, 508))})
        self.assertEqual(listing[0]["section_ids"], [self.sections[listing[0]["sections"][0]]["id"]])
        # A section is seated once per exam; another exam may seat it again.
        again = self.create(section_ids=[self.sections[507]["id"]], room_id=self.small["id"])
        self.assertEqual(again.status_code, 409, again.json)
        self.assertEqual(again.json["error"], "Already seated for Test 1: Section 507 in HECC 105. Remove that chart first, or leave the section out.")
        self.assertEqual(self.create(assignment_id=self.quiz["id"], section_ids=[self.sections[507]["id"]]).status_code, 201)
        self.assertEqual(len(self.client.get("/api/state").json["seating_charts"]), 3)

    def test_mixed_sections_are_shuffled_across_rooms_by_capacity(self):
        both = [self.sections[507]["id"], self.sections[508]["id"]]
        pool = {"room_ids": [self.hall["id"], self.small["id"]], "section_ids": both}
        options = self.client.get(f"/api/seating/options?assignment_id={self.assignment['id']}&sections={','.join(both)}&rooms={self.hall['id']},{self.small['id']}").json
        self.assertEqual(sum(item["to_seat"] for item in options["pool"]), 7)
        for payload, message in (({"pool": {**pool, "room_ids": [self.hall["id"]]}}, "at least two rooms"),
                                 ({"pool": {**pool, "section_ids": []}}, "at least one section"),
                                 ({"pool": pool, "allocations": []}, "Either mix")):
            with self.subTest(payload=payload):
                response = self.client.post("/api/seating/plan", json={"assignment_id": self.assignment["id"], **payload}, headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn(message, response.json["error"])
        created = self.client.post("/api/seating/plan", json={"assignment_id": self.assignment["id"], "pool": pool, "exam_version_count": 2}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        hall, tiny = created.json["charts"]
        shares = [item["to_seat"] for item in options["pool"]]
        self.assertEqual([hall["counts"]["seated"], tiny["counts"]["seated"]], shares)
        self.assertEqual((hall["sections"], tiny["sections"], hall["pool_rooms"], tiny["pool_rooms"]), ([507, 508], [507, 508], ["Tiny"], ["HECC 105"]))
        self.assertTrue(hall["check"]["ok"] and tiny["check"]["ok"], (hall["issues"], tiny["issues"]))
        seated = [student["enrollment_id"] for chart in (hall, tiny) for student in chart["students"] if student["seat"]]
        self.assertEqual(len(seated), 7)
        self.assertEqual(len(set(seated)), 7)
        # The DR students are listed, counted and given cards on the first room only.
        self.assertEqual((hall["live_counts"]["excluded_dr"], tiny["live_counts"]["excluded_dr"]), (2, 0))
        self.assertEqual([student["status"] for student in tiny["students"]], ["seated"] * shares[1])
        self.assertEqual(sum(tiny["version_totals"].values()) + sum(hall["version_totals"].values()), 7)
        # Updating one room leaves the students the other room seats where they are.
        updated = self.client.post(f"/api/seating/{tiny["id"]}/update", json={"version": tiny["version"]}, headers=HEADERS)
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json["counts"]["seated"], shares[1])
        regenerated = self.client.post(f"/api/seating/{tiny["id"]}/regenerate", json={"version": updated.json["version"]}, headers=HEADERS)
        self.assertEqual((regenerated.status_code, regenerated.json["counts"]["seated"]), (200, shares[1]), regenerated.json)
        # A student seated in the hall cannot also be seated in the small room.
        hall_student = next(student for student in hall["students"] if student["seat"])
        free = next(seat for seat in regenerated.json["room"]["seats"] if not seat["student"] and not seat["broken"] and not seat["blocked"])
        moved = self.client.post(f"/api/seating/{tiny['id']}/move", json={"version": regenerated.json["version"], "enrollment_id": hall_student["enrollment_id"],
                                                                         "seat_id": free["id"]}, headers=HEADERS)
        self.assertEqual(moved.status_code, 409, moved.json)
        self.assertIn("seated in HECC 105", moved.json["error"])
        # Removing a room leaves its students unseated in the other, where Update seating seats them.
        self.assertEqual(self.client.delete(f"/api/seating/{tiny['id']}", json={"version": regenerated.json["version"]}, headers=HEADERS).status_code, 204)
        hall = self.client.get(f"/api/seating/{hall['id']}").json
        self.assertEqual((hall["check"]["counts"].get("missing_seat"), hall["pool_rooms"]), (shares[1], []))
        hall = self.client.post(f"/api/seating/{hall["id"]}/update", json={"version": hall["version"]}, headers=HEADERS).json
        self.assertEqual((hall["counts"]["seated"], hall["check"]["ok"]), (7, True))

    def test_pool_shares_follow_spaced_then_usable_seats(self):
        from seating import SeatingService
        rooms = [{"usable_count": 100, "spaced_count": 50}, {"usable_count": 40, "spaced_count": 20}, {"usable_count": 10, "spaced_count": 0}]
        self.assertEqual(SeatingService.pool_shares(rooms, 35), [24, 10, 1])
        self.assertEqual(SeatingService.pool_shares(rooms, 140), [94, 37, 9])
        self.assertEqual(sum(SeatingService.pool_shares(rooms, 150)), 150)
        with self.assertRaises(Exception) as raised:
            SeatingService.pool_shares(rooms, 151)
        self.assertIn("only 150 usable seats together", raised.exception.description)

    def test_generation_skips_dr_booked_students_and_uses_usable_seats(self):
        response = self.create(notes=" Bring calculators ")
        self.assertEqual(response.status_code, 201, response.json)
        chart = response.json
        self.assertEqual((chart["title"], chart["code"], chart["term_label"], chart["sections"], chart["room_name"], chart["notes"]),
                         ("Test 1", "MATH 140", "Fall 2026", [507, 508], "HECC 105", "Bring calculators"))
        self.assertEqual(chart["counts"], {"students": 9, "excluded_dr": 2, "seated": 7, "free_seats": 147, "usable_seats": 154,
                                           "spaced": 7, "adjacent_pairs": 0, "spaced_capacity": HALL_SPACED, "check_ok": True})
        self.assertEqual(chart["spacing"], {"seated": 7, "spaced": 7, "crowded": 0, "adjacent_pairs": 0, "spaced_capacity": HALL_SPACED})
        # The generation-time roster check is recorded, and the live check agrees.
        self.assertEqual(chart["checked"], {"ok": True, "counts": {"dr_seated": 0, "missing_seat": 0, "not_in_roster": 0, "seat_unusable": 0},
                                            "students": 9, "excluded_dr": 2, "dr_seated": [], "missing_seat": [], "not_in_roster": [], "seat_unusable": []})
        self.assertEqual((chart["check"]["ok"], chart["checked_at"] != "", chart["events"][0]["detail"]["check"]["ok"], chart["counts"]["check_ok"]), (True, True, True, True))
        self.assertFalse(any(seat["student"]["crowded"] for seat in chart["room"]["seats"] if seat["student"]))
        students = self.by_name(chart)
        self.assertEqual((students["Example, Alice"]["status"], students["Example, Alice"]["seat"]), ("dr_testing", None))
        self.assertEqual(students["Example, Carol"]["status"], "dr_testing")
        self.assertEqual(students["Example, Bob"]["status"], "seated")  # Bob's booking is for the Final.
        seated = [student for student in chart["students"] if student["status"] == "seated"]
        self.assertEqual(len(seated), 7)
        self.assertEqual(len({student["seat"] for student in seated}), 7)
        labels = {seat["label"]: seat for seat in chart["room"]["seats"]}
        self.assertTrue(all(not labels[student["seat"]]["broken"] and not labels[student["seat"]]["blocked"] for student in seated))
        self.assertEqual(sum(1 for seat in chart["room"]["seats"] if seat["student"]), 7)
        self.assertEqual(chart["issues"], [])
        self.assertEqual(chart["events"][0]["kind"], "regenerate")
        self.assertEqual(sorted(chart["events"][0]["detail"]["excluded_dr"]), sorted([students["Example, Alice"]["enrollment_id"], students["Example, Carol"]["enrollment_id"]]))
        self.assertEqual(chart["dr_exam_label"], "Test 1")
        summary = self.client.get("/api/state").json["seating_charts"][0]
        self.assertEqual((summary["seated_count"], summary["counts"]["excluded_dr"], summary["room_name"]), (7, 2, "HECC 105"))
        again = self.client.post(f"/api/seating/{chart['id']}/regenerate", json={"version": chart["version"]}, headers=HEADERS).json
        before = {student["name"]: student["seat"] for student in seated}
        after = {student["name"]: student["seat"] for student in again["students"] if student["status"] == "seated"}
        self.assertEqual(set(before), set(after))
        self.assertNotEqual(before, after)
        self.assertEqual(self.client.post(f"/api/seating/{chart['id']}/regenerate", json={"version": chart["version"]}, headers=HEADERS).status_code, 409)

    def test_list_rows_carry_card_and_version_totals(self):
        # Read-only list additions for the seating list's status chips: counts only, no identities.
        chart = self.create().json
        row = self.client.get("/api/seating").json[0]
        self.assertEqual(row["card_counts"], {"recipients": 0, "responded": 0, "flagged": 0})
        self.assertEqual(row["version_totals"], {})
        assigned = self.client.post(f"/api/seating/{chart['id']}/exam-versions", json={"version": chart["version"], "count": 2}, headers=HEADERS)
        self.assertEqual(assigned.status_code, 200, assigned.json)
        row = self.client.get("/api/seating").json[0]
        self.assertEqual(set(row["version_totals"]), {"A", "B"})
        self.assertEqual(sum(row["version_totals"].values()), row["seated_count"])
        self.assertLessEqual(abs(row["version_totals"]["A"] - row["version_totals"]["B"]), 1)

    def test_an_exams_page_of_rooms_opens_in_its_assignment(self):
        # SEATING "One tile per exam": '#seating/exam/<assignment>/<term>' resolves like a chart of that exam, and an exam
        # with no chart is no assignment's page.
        def resolve(route):
            response = self.client.get("/api/assignments/resolve", query_string={"route": route})
            self.assertEqual(response.status_code, 200, response.json)
            return response.json["context"]

        chart = self.create().json
        context = resolve(f"#seating/exam/{chart['assignment_id']}/{chart['term_id']}")
        self.assertEqual((context["assignment_id"], context["term_id"]), (chart["assignment_id"], chart["term_id"]))
        self.assertEqual(context, resolve(f"#seating/{chart['id']}"))
        self.assertIsNone(resolve(f"#seating/exam/{self.quiz['id']}/{chart['term_id']}"))
        self.assertIsNone(resolve(f"#seating/exam/{chart['assignment_id']}"))

    def test_too_many_students_is_rejected_without_creating_a_chart(self):
        response = self.create(room_id=self.small["id"])
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn("7 students need seats but Tiny has only 3 usable seats free: 4 seats, 1 broken, 0 blocked", response.json["error"])
        self.assertIn("2 students with DR exam bookings are not counted", response.json["error"])
        self.assertEqual(self.client.get("/api/seating").json, [])
        fits = self.create(room_id=self.small["id"], section_ids=[self.sections[508]["id"]])
        self.assertEqual(fits.status_code, 201, fits.json)
        self.assertEqual((fits.json["counts"]["seated"], fits.json["counts"]["free_seats"], fits.json["counts"]["adjacent_pairs"]), (2, 1, 0))
        self.assertEqual({student["seat"] for student in fits.json["students"] if student["status"] == "seated"}, {"A1", "A3"})
        self.client.post("/api/courses", json={"name": "Stats", "code": "STAT 211", "term": "Fall", "year": 2026, "sections": [1]}, headers=HEADERS)
        stat = next(item for item in self.client.get("/api/seating/catalog").json["sections"] if item["code"] == "STAT 211")
        assignment = self.client.post("/api/documents/assignments", json={"definition_id": next(d["id"] for d in self.client.get("/api/state").json["course_definitions"] if d["code"] == "STAT 211"), "key": "exam", "title": "Exam"}, headers=HEADERS).json
        nobody = self.create(assignment_id=assignment["id"], section_ids=[stat["id"]])
        self.assertEqual(nobody.status_code, 400)
        self.assertIn("no roster students", nobody.json["error"])

    def test_update_keeps_seats_and_reacts_to_room_roster_and_dr_changes(self):
        chart = self.create().json
        bob = self.by_name(chart)["Example, Bob"]
        seat = next(seat for seat in chart["room"]["seats"] if seat["label"] == bob["seat"])
        room = self.client.get(f"/api/rooms/{self.hall['id']}").json
        self.client.post(f"/api/rooms/{self.hall['id']}/seats", json={"version": room["version"], "seat_ids": [seat["id"]], "blocked": True}, headers=HEADERS)
        stale = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(stale["issues"], [f"Example, Bob is seated at {bob['seat']}, which is now blocked."])
        self.assertEqual(self.by_name(stale)["Example, Bob"]["status"], "seat_unusable")
        self.import_dr([booking(UIN="000000003", **{"Last Name": "Student", "First Name": "3"})])
        self.client.post(f"/api/course-groups/{self.group['id']}/sections/508/roster",
                         data={"file": (io.BytesIO(csv_bytes([student_row("Student, 10", "000000010")])), "roster.csv")}, headers=HEADERS)
        pending = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(sorted(pending["issues"]), sorted([f"Example, Bob is seated at {bob['seat']}, which is now blocked.",
                                                            f"Student, 3 has a DR exam booking for Test 1 but is seated at {self.by_name(chart)['Student, 3']['seat']}.",
                                                            "Student, 10 has no seat."]))
        self.assertEqual(pending["live_counts"]["unseated"], 1)
        self.assertEqual(pending["check"], {"ok": False, "counts": {"dr_seated": 1, "missing_seat": 1, "not_in_roster": 0, "seat_unusable": 1}})
        self.assertEqual(pending["checked"]["ok"], True)  # The recorded check reflects generation time.
        listed = self.client.get("/api/state").json["seating_charts"][0]
        self.assertEqual((listed["check"]["ok"], listed["check"]["counts"]["dr_seated"], listed["checked"]["ok"]), (False, 1, True))
        updated = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": pending["version"]}, headers=HEADERS).json
        self.assertEqual(updated["issues"], [])
        self.assertEqual((updated["check"]["ok"], updated["checked"]["ok"], updated["events"][0]["detail"]["check"]["counts"]["dr_seated"]), (True, True, 0))
        before = {student["name"]: student["seat"] for student in chart["students"] if student["status"] == "seated"}
        after = self.by_name(updated)
        for name in ("Student, 4", "Student, 5", "Student, 6", "Student, 8", "Student, 9"):
            self.assertEqual(after[name]["seat"], before[name])
        self.assertNotEqual(after["Example, Bob"]["seat"], bob["seat"])
        self.assertEqual(after["Student, 3"]["status"], "dr_testing")
        self.assertEqual(after["Student, 10"]["status"], "seated")
        self.assertEqual({key: updated["counts"][key] for key in ("students", "excluded_dr", "seated", "free_seats", "usable_seats", "spaced", "adjacent_pairs")},
                         {"students": 10, "excluded_dr": 3, "seated": 7, "free_seats": 146, "usable_seats": 153, "spaced": 7, "adjacent_pairs": 0})
        detail = updated["events"][0]["detail"]
        self.assertEqual((detail["mode"], detail["kept"], detail["newly_seated"]), ("update", 5, 2))
        self.assertEqual(sorted(item["reason"] for item in detail["removed"]), ["DR exam booking", "seat is no longer usable"])

    def drop(self, number, name, action="drop"):
        roster = self.client.get(f"/api/course-groups/{self.group['id']}/sections/{number}/roster").json
        student = next(s for s in roster["students"] if s["fields"].get("Name") == name)
        answer = self.client.post(f"/api/course-groups/{self.group['id']}/sections/{number}/roster/{action}",
                                  json={"enrollment_ids": [student["id"]], "request_id": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)

    def test_q_drops_and_withdrawals_get_no_seat_and_give_up_one_they_hold(self):
        """
        The registrar's marks come with a roster import, which since 2026-10-25 also drops them; a seat they hold is given up at
        the next update."""
        chart = self.create().json
        held = self.by_name(chart)["Student, 5"]["seat"]
        self.assertTrue(held)
        rows = [row.copy() for row in SECTION_507]
        reg, final = COLUMNS.index("Reg\nStatus"), COLUMNS.index("Final")
        rows[4][reg], rows[4][final] = "Q-Drop State Limited", "Q"        # Student, 5
        rows[3][reg], rows[3][final] = "Withdraw After Census Date", "W"  # Student, 4
        imported = self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster",
                                    data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertEqual((imported.status_code, imported.json["dropped_count"]), (201, 2))  # both dropped by the import
        options = self.client.get(f"/api/seating/options?assignment_id={self.assignment['id']}&section_ids={self.sections[507]['id']}").json
        self.assertNotIn("Student, 4", json.dumps(options))
        now = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual((self.by_name(now)["Student, 5"]["seat"], self.by_name(now)["Student, 5"]["leaving"]), (held, "Q drop"))  # until the update
        updated = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": now["version"]}, headers=HEADERS).json
        self.assertFalse({"Student, 4", "Student, 5"} & set(self.by_name(updated)))
        self.assertEqual(sorted(item["reason"] for item in updated["events"][0]["detail"]["removed"]),
                         ["Q drop on the registrar's roster", "Withdrawn on the registrar's roster"])
        self.assertEqual(updated["issues"], [])
        # Restored by the instructor: seated again at the next update.
        self.drop(507, "Student, 5")
        self.drop(507, "Student, 5", "restore")
        back = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": self.client.get(f"/api/seating/{chart['id']}").json["version"]},
                                headers=HEADERS).json
        self.assertTrue(self.by_name(back)["Student, 5"]["seat"])

    def test_dropped_students_are_never_newly_seated_but_keep_a_seat_they_hold(self):
        """requirement, 2026-09-27: a dropped student keeps what is recorded (a seat they hold) and leaves new seating."""
        self.drop(507, "Student, 4")
        options = self.client.get(f"/api/seating/options?assignment_id={self.assignment['id']}&section_ids={self.sections[507]['id']}").json
        chart = self.create().json
        self.assertNotIn("Student, 4", self.by_name(chart))
        self.assertEqual(chart["issues"], [])
        held = self.by_name(chart)["Student, 5"]["seat"]
        self.drop(507, "Student, 5")
        now = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual((self.by_name(now)["Student, 5"]["seat"], now["issues"]), (held, []))
        updated = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": now["version"]}, headers=HEADERS).json
        self.assertEqual(self.by_name(updated)["Student, 5"]["seat"], held)
        self.assertNotIn("Student, 4", self.by_name(updated))
        regenerated = self.client.post(f"/api/seating/{chart['id']}/regenerate", json={"version": updated["version"]}, headers=HEADERS).json
        self.assertNotIn("Student, 5", self.by_name(regenerated))
        self.assertIn("dropped from the roster", [item["reason"] for item in regenerated["events"][0]["detail"]["removed"]])
        # Restored: an update seats them again.
        self.drop(507, "Student, 4", "restore")
        restored = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertIn("Student, 4 has no seat.", restored["issues"])
        self.assertTrue(options)

    def test_room_chart_replacement_and_room_deletion_guard(self):
        chart = self.create(room_id=self.small["id"], section_ids=[self.sections[508]["id"]]).json
        seated = {student["seat"]: student["name"] for student in chart["students"] if student["status"] == "seated"}
        self.assertEqual(len(seated), 2)
        room = self.client.get(f"/api/rooms/{self.small['id']}").json
        removed = next(iter(seated))
        keep = {"A1": "A1", "B1": "A2", "C1": "A3", "A2": "B1"}
        keep = {cell: label for cell, label in keep.items() if label != removed}
        replaced = self.client.post(f"/api/rooms/{self.small['id']}/chart", data={"version": str(room["version"]), "file": (io.BytesIO(chart_bytes(keep)), "v2.xlsx")}, headers=HEADERS)
        self.assertEqual(replaced.status_code, 200, replaced.json)
        after = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(after["issues"], [f"{seated[removed]} has no seat."])
        fixed = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": after["version"]}, headers=HEADERS)
        self.assertEqual(fixed.status_code, 200, fixed.json)
        self.assertEqual(fixed.json["issues"], [])
        blocked = self.client.delete(f"/api/rooms/{self.small['id']}", json={"version": replaced.json["version"]}, headers=HEADERS)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("used by 1 seating chart", blocked.json["error"])
        self.assertEqual(self.client.delete(f"/api/seating/{chart['id']}", json={"version": fixed.json["version"]}, headers=HEADERS).status_code, 204)
        self.assertEqual(self.client.get(f"/api/seating/{chart['id']}").status_code, 404)
        room = self.client.get(f"/api/rooms/{self.small['id']}").json
        self.assertEqual(self.client.delete(f"/api/rooms/{self.small['id']}", json={"version": room["version"]}, headers=HEADERS).status_code, 204)

    def test_spacing_prefers_an_empty_seat_between_students_and_degrades_when_full(self):
        # Three students in a three-seat row: nobody can be spaced, but seating still succeeds.
        full = self.create(assignment_id=self.quiz["id"], room_id=self.small["id"], section_ids=[self.sections[508]["id"]]).json
        self.assertEqual({key: full["counts"][key] for key in ("seated", "spaced", "adjacent_pairs", "spaced_capacity")},
                         {"seated": 3, "spaced": 0, "adjacent_pairs": 2, "spaced_capacity": 2})
        self.assertEqual((full["spacing"]["crowded"], full["issues"]), (3, []))
        self.assertTrue(all(seat["student"]["crowded"] for seat in full["room"]["seats"] if seat["student"]))
        # A large room spaces everyone, and stays spaced through regeneration and updates.
        chart = self.create().json
        for _ in range(5):
            chart = self.client.post(f"/api/seating/{chart['id']}/regenerate", json={"version": chart["version"]}, headers=HEADERS).json
            self.assertEqual((chart["spacing"]["spaced"], chart["spacing"]["adjacent_pairs"]), (7, 0))
        self.client.post(f"/api/course-groups/{self.group['id']}/sections/508/roster",
                         data={"file": (io.BytesIO(csv_bytes([student_row(f"Student, {index}", f"0000000{index}") for index in range(10, 30)])), "roster.csv")}, headers=HEADERS)
        updated = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": chart["version"]}, headers=HEADERS).json
        self.assertEqual((updated["counts"]["seated"], updated["spacing"]["spaced"], updated["spacing"]["adjacent_pairs"]), (27, 27, 0))
        seats = {seat["id"]: seat for seat in updated["room"]["seats"]}
        rows = {}
        for student in updated["students"]:
            if student["seat_id"]:
                rows.setdefault(seats[student["seat_id"]]["row"], []).append(seats[student["seat_id"]]["column"])
        for columns in rows.values():
            ordered = sorted(columns)
            self.assertTrue(all(b - a >= 2 for a, b in zip(ordered, ordered[1:])), ordered)

    def test_roster_check_is_independent_of_placement(self):
        chart = self.create().json
        alice = self.by_name(chart)["Example, Alice"]
        free = next(seat for seat in chart["room"]["seats"] if not seat["student"] and not seat["broken"] and not seat["blocked"])
        # Slip a DR-booked student into a seat behind the service's back; the check must catch it.
        from rosters import connect
        with connect(self.app.extensions["rosters"].path) as db:
            db.execute("INSERT INTO seating_assignments VALUES (?, ?, ?, ?, ?)", (chart["id"], free["id"], alice["section_id"], alice["enrollment_id"], "2026-09-14T00:00:00+00:00"))
        flagged = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(flagged["check"], {"ok": False, "counts": {"dr_seated": 1, "missing_seat": 0, "not_in_roster": 0, "seat_unusable": 0}})
        self.assertEqual(flagged["issues"], [f"Example, Alice has a DR exam booking for Test 1 but is seated at {free['label']}."])
        self.assertEqual(self.by_name(flagged)["Example, Alice"]["status"], "dr_testing")
        self.assertFalse(self.client.get("/api/seating").json[0]["check"]["ok"])
        fixed = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": flagged["version"]}, headers=HEADERS).json
        self.assertEqual((fixed["check"]["ok"], fixed["issues"], self.by_name(fixed)["Example, Alice"]["seat"]), (True, [], None))
        self.assertEqual(fixed["events"][0]["detail"]["removed"][0]["reason"], "DR exam booking")

    def test_confirm_exports_print_jobs_and_locks_deletion(self):
        chart = self.create().json
        route = f"/api/seating/{chart['id']}"
        options = self.client.get(route + "/print-options").json
        self.assertEqual((options["versions"], options["issues"], options["confirmed_at"]), ([], [], ""))
        self.assertEqual([(item["number"], item["seated"]) for item in options["sections"]], [(507, 5), (508, 2)])
        # Without an attached PDF, a plain export is refused and stamps-only needs a page count.
        refused = self.client.post(route + "/confirm", json={"version": chart["version"], "mode": "print", "request_id": "c1"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("No exam PDF is attached to Test 1", refused.json["error"])
        for payload, status, message in (
            ({"mode": "stamps_only", "request_id": "c1"}, 400, "between 1 and 100 pages"),
            ({"mode": "stamps_only", "page_count": 3}, 400, "request ID"),
            ({"mode": "stamps_only", "page_count": 3, "request_id": "c1", "extras": 101}, 400, "between 0 and 100"),
            ({"mode": "banana", "request_id": "c1"}, 400, "stamps only"),
        ):
            with self.subTest(payload=payload):
                response = self.client.post(route + "/confirm", json={"version": chart["version"], **payload}, headers=HEADERS)
                self.assertEqual(response.status_code, status, response.json)
                self.assertIn(message, response.json["error"])
        self.assertEqual(self.client.post(route + "/confirm", json={"version": 99, "mode": "stamps_only", "page_count": 3, "request_id": "c1"}, headers=HEADERS).status_code, 409)
        # Unresolved issues must be acknowledged.
        seat = next(item for item in chart["room"]["seats"] if item["student"])
        room = self.client.get(f"/api/rooms/{self.hall['id']}").json
        self.client.post(f"/api/rooms/{self.hall['id']}/seats", json={"version": room["version"], "seat_ids": [seat["id"]], "blocked": True}, headers=HEADERS)
        blocked = self.client.post(route + "/confirm", json={"version": chart["version"], "mode": "stamps_only", "page_count": 3, "request_id": "c1"}, headers=HEADERS)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("1 unresolved issue", blocked.json["error"])
        self.assertIn("continue anyway", blocked.json["error"])
        self.assertEqual(self.client.get("/api/seating").json[0]["confirmed_at"], "")
        # Without the review window's exam date, cards cannot be prepared; the print path itself is unaffected.
        self.assertIn("exam date", self.client.post(route + "/confirm", json={"version": chart["version"], "mode": "stamps_only", "page_count": 3, "request_id": "c1", "acknowledge_issues": True}, headers=HEADERS).json["error"])
        confirmed = self.client.post(route + "/confirm", json={"version": chart["version"], "mode": "stamps_only", "page_count": 3, "request_id": "c1", "extras": 1, "acknowledge_issues": True, "prepare_cards": False}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        result = confirmed.json
        self.assertEqual((result["confirmed_mode"], result["delete_confirmation"], result["version"]), ("stamps_only", "typed", chart["version"] + 1))
        self.assertTrue(result["confirmed_at"])
        self.assertEqual([(item["section_number"], item["quantity"], item["status"]) for item in result["exported_batches"]], [(507, 6), (508, 3)][:0] or [(507, 6, "pending"), (508, 3, "pending")])
        self.assertEqual(len(result["print_batches"]), 2)
        self.assertEqual(result["events"][0]["kind"], "confirmed")
        self.assertEqual(len(result["events"][0]["detail"]["acknowledged_issues"]), 1)
        versions = self.client.get("/api/documents/catalog").json["versions"]
        stamps = next(item for item in versions if item["assignment_id"] == self.assignment["id"])
        self.assertEqual((stamps["label"], stamps["page_count"]), ("Stamps only · 3 pages", 3))
        batch = self.client.get(f"/api/documents/batches/{result['exported_batches'][0]['id']}").json
        self.assertEqual((batch["assignment_version_id"], batch["seating_chart_id"], len(batch["copies"])), (stamps["id"], chart["id"], 6))
        self.assertTrue(batch["copies"][0]["caption"].startswith("Seat "))
        # A retry with the same request ID reuses the print jobs; a new page count reuses nothing but the chart.
        again = self.client.post(route + "/confirm", json={"version": result["version"], "mode": "stamps_only", "page_count": 3, "request_id": "c1", "extras": 1, "acknowledge_issues": True, "prepare_cards": False}, headers=HEADERS).json
        self.assertEqual([item["id"] for item in again["exported_batches"]], [item["id"] for item in result["exported_batches"]])
        self.assertEqual(len(self.client.get("/api/documents/catalog").json["batches"]), 2)
        # Confirmation alone (print jobs cancelled) still requires typed deletion.
        for item in result["exported_batches"]:
            self.assertEqual(self.client.post(f"/api/documents/batches/{item['id']}/cancel", json={}, headers=HEADERS).status_code, 200)
        latest = self.client.get(route).json
        self.assertEqual((latest["print_jobs"], latest["delete_confirmation"]), (0, "typed"))
        refused = self.client.delete(route, json={"version": latest["version"]}, headers=HEADERS)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("confirmed for printing on", refused.json["error"])
        self.assertEqual(self.client.delete(route, json={"version": latest["version"], "confirmation": "delete"}, headers=HEADERS).status_code, 204)
        # With a real PDF attached, the plain export uses the chosen version and builds normally.
        chart = self.create(assignment_id=self.quiz["id"], section_ids=[self.sections[508]["id"]]).json
        version = self.client.post("/api/documents/versions/fixture", json={"assignment_id": self.quiz["id"], "label": "A", "paper": "letter"}, headers=HEADERS).json
        options = self.client.get(f"/api/seating/{chart['id']}/print-options").json
        self.assertEqual([item["id"] for item in options["versions"]], [version["id"]])
        exported = self.client.post(f"/api/seating/{chart['id']}/confirm", json={"version": chart["version"], "request_id": "c2", "prepare_cards": False}, headers=HEADERS).json
        self.assertEqual((exported["confirmed_mode"], exported["confirmed_version_id"], [item["quantity"] for item in exported["exported_batches"]]), ("print", version["id"], [3]))
        self.assertTrue(self.app.extensions["documents"].build_one())
        self.assertEqual(self.client.get(f"/api/documents/batches/{exported['exported_batches'][0]['id']}").json["status"], "ready")

    def test_confirmation_prepares_seat_and_dr_cards_from_the_review_window(self):
        import hashlib
        from response_cards.mail import MailConfig
        from test_response_card_mail import FakeRelay
        from test_response_cards import FakeRemote
        cards = self.app.extensions["response_cards"]
        cards.remote = FakeRemote()
        relay = FakeRelay()
        cards.mail.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4)
        cards.mail.transport = relay
        # Alice holds two bookings linked to Test 1 ("Test 1" and "Exam 1"), so she cannot get a single-schedule card.
        self.import_dr([booking(**{"Exam Type": "Exam 1", "Start Time": "09:00 AM", "End Time": "10:53 AM"})])
        chart = self.create().json
        route = f"/api/seating/{chart['id']}"
        templates = self.client.get(route + "/card-templates").json
        self.assertEqual((templates["available"], templates["configured"], templates["mail_configured"], templates["exam_date"], templates["exam_time"], templates["counts"]),
                         (True, True, True, "", "", {"seated": 7, "dr": 1, "dr_ambiguous": ["Example, Alice"]}))
        self.assertEqual([field["id"] for field in templates["seat"]["fields"]], ["confirm_room", "confirm_seat", "confirm_uin"])
        self.assertEqual([field["id"] for field in templates["dr"]["fields"]], ["confirm_dr_date", "confirm_dr_time", "confirm_dr_location", "confirm_uin"])
        base = {"version": chart["version"], "mode": "stamps_only", "page_count": 2, "request_id": "cards-1"}
        for payload, message in (({}, "Enter the exam date"), ({"exam_date": "Friday"}, "YYYY-MM-DD"), ({"exam_date": "2026-09-25"}, "Enter the exam time"),
                                 ({"exam_date": "2026-09-25", "exam_time": "7:00 PM", "cards": {"seat": {"title": ""}}}, "Title"),
                                 ({"exam_date": "2026-09-25", "exam_time": "7:00 PM", "publish_cards": "yes"}, "publish")):
            with self.subTest(payload=payload):
                response = self.client.post(route + "/confirm", json={**base, **payload}, headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn(message, response.json["error"])
        # The review window adds a checkbox and a custom question to the seat card; this first run keeps the cards local.
        seat_schema = templates["seat"]
        seat_schema["fields"] = seat_schema["fields"][:2] + [
            {"id": "custom_1", "type": "acknowledgment", "label": "I will bring a pencil.", "required": True},
            {"id": "custom_2", "type": "short_text", "label": "Which calculator will you bring?", "required": True}]
        confirmed = self.client.post(route + "/confirm", json={**base, "exam_date": "2026-09-25", "exam_time": " 7:00  PM ", "publish_cards": False,
                                                                "cards": {"seat": seat_schema, "dr": templates["dr"]}}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        result = confirmed.json
        self.assertEqual((result["exam_date"], result["exam_time"]), ("2026-09-25", "7:00 PM"))
        prepared = result["response_cards"]
        self.assertEqual((prepared["seat"]["recipients"], prepared["seat"]["published"], prepared["dr"]["recipients"], prepared["dr"]["published"], prepared["published"], prepared["emailed"]), (7, 0, 1, 0, False, False))
        self.assertEqual(prepared["errors"], ["Example, Alice has no single DR booking linked to Test 1, so no DR notice was prepared; notify them directly."])
        self.assertEqual((cards.remote.calls, relay.sent), ([], []))  # Nothing published or sent.
        seat_issue = cards.get_issue(prepared["seat"]["issue_id"])
        self.assertEqual((seat_issue["seating_chart_id"], seat_issue["counts"]["total"], seat_issue["counts"].get("published", 0)), (chart["id"], 7, 0))
        names = {card["student_name"]: card for card in seat_issue["recipients"]}
        self.assertNotIn("Example, Alice", names)
        self.assertNotIn("Example, Carol", names)
        bob = names["Example, Bob"]
        seat_label = self.by_name(chart)["Example, Bob"]["seat"]
        self.assertEqual(bob["schema"]["title"], "Test 1 · your seat assignment")
        self.assertEqual(bob["schema"]["description"], f"Your Test 1 for MATH 140 is on Friday, September 25, 2026 at 7:00 PM in HECC 105. Your assigned seat is {seat_label}. Please go directly to that seat and bring your student ID.")
        fields = {field["id"]: field for field in bob["schema"]["fields"]}
        self.assertEqual([field["id"] for field in bob["schema"]["fields"]], ["confirm_room", "confirm_seat", "custom_1", "custom_2", "confirm_uin"])
        self.assertEqual(fields["confirm_room"]["verification"], {"expected": "HECC 105", "mode": "compact", "on_mismatch": "BLOCK_SUBMISSION"})
        self.assertEqual(fields["confirm_seat"]["verification"], {"expected": seat_label, "mode": "compact", "on_mismatch": "BLOCK_SUBMISSION"})
        self.assertEqual((fields["custom_1"]["type"], fields["custom_1"]["required"], fields["custom_2"]["type"], "verification" in fields["custom_2"]), ("acknowledgment", True, "short_text", False))
        uin = fields["confirm_uin"]["verification"]
        self.assertEqual((uin["mode"], uin["on_mismatch"], fields["confirm_uin"]["label"]), ("uin-digest", "BLOCK_SUBMISSION", "Type your UIN to confirm that you have read and understood all of the above."))
        salt, digest = uin["expected"].split(":")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{16}", salt))
        self.assertEqual(digest, hashlib.sha256((salt + "123456789").encode()).hexdigest())
        self.assertNotIn("123456789", json.dumps(bob["schema"]))
        dr_issue = cards.get_issue(prepared["dr"]["issue_id"])
        carol = dr_issue["recipients"][0]
        self.assertEqual((dr_issue["assignment_id"], dr_issue["seating_chart_id"], carol["student_name"]), (self.assignment["id"], None, "Example, Carol"))
        self.assertEqual(carol["schema"]["description"], "Records show that you have registered to take Test 1 with the Disability Resource Center. You MUST take your exam as scheduled with the Disability Resource Center. If this is an error, you must inform Professor Sinkule immediately.")
        self.assertNotIn("2026-09-17", carol["schema"]["description"])
        dr_fields = {field["id"]: field for field in carol["schema"]["fields"]}
        self.assertEqual(dr_fields["confirm_dr_date"]["verification"], {"expected": "2026-09-17", "mode": "exact", "on_mismatch": "ALLOW_AND_FLAG"})
        self.assertEqual(dr_fields["confirm_dr_time"]["verification"], {"expected": "19:05", "mode": "exact", "on_mismatch": "ALLOW_AND_FLAG"})
        self.assertNotIn("verification", dr_fields["confirm_dr_location"])  # RESPONSE-CARDS "DR LOCATION IS NOT CHECKED"
        self.assertEqual(carol["schema"]["fields"][-1]["id"], "confirm_uin")
        self.assertEqual(hashlib.sha256((dr_fields["confirm_uin"]["verification"]["expected"].split(":")[0] + "222222222").encode()).hexdigest(), dr_fields["confirm_uin"]["verification"]["expected"].split(":")[1])
        # The edited seat card became the course template, and the templates carry the UIN check last.
        saved = {item["name"]: item for item in cards.templates(self.definition_id)}
        self.assertEqual([field["id"] for field in saved["Exam seat assignment (Faculty)"]["schema"]["fields"]], ["confirm_room", "confirm_seat", "custom_1", "custom_2", "confirm_uin"])
        self.assertEqual(self.client.get(route + "/card-templates").json["exam_time"], "7:00 PM")
        detail = self.client.get(route).json
        self.assertEqual((detail["response_cards"]["seat"]["recipients"], detail["response_cards"]["dr"]["recipients"], len(detail["response_cards"]["errors"])), (7, 1, 1))
        states = lambda payload: {item["name"]: item["card"] for item in payload["students"] if item.get("card")}
        self.assertEqual({item["state"] for item in states(detail).values()} - {"issue"}, {"pending"})
        self.assertEqual(states(detail)["Example, Bob"]["detail"], ["Card prepared, not yet published."])
        self.assertEqual(states(detail)["Example, Alice"]["summary"], "No response card")
        self.assertEqual({(item["can_resend"], item["link_available"]) for item in states(detail).values()}, {(False, False)})  # Nothing is published yet.
        # A retry reuses the issues; opting out prepares nothing; the default publishes and emails every card.
        again = self.client.post(route + "/confirm", json={**base, "version": detail["version"], "exam_date": "2026-09-25", "exam_time": "7:00 PM", "publish_cards": False}, headers=HEADERS).json
        self.assertEqual(again["response_cards"]["seat"]["issue_id"], prepared["seat"]["issue_id"])
        skipped = self.client.post(route + "/confirm", json={**base, "version": again["version"], "request_id": "cards-2", "prepare_cards": False}, headers=HEADERS).json
        self.assertEqual(skipped["response_cards"]["seat"]["issue_id"], prepared["seat"]["issue_id"])  # Skipping keeps the record of earlier cards.
        published = self.client.post(route + "/confirm", json={**base, "version": skipped["version"], "request_id": "cards-3", "exam_date": "2026-09-25", "exam_time": "7:00 PM"}, headers=HEADERS).json
        rc = published["response_cards"]
        self.assertEqual((rc["published"], rc["emailed"], rc["seat"]["published"], rc["seat"]["queued"], rc["seat"]["emailed"], rc["dr"]["published"], rc["dr"]["queued"], rc["dr"]["emailed"]),
                         (True, True, 7, 7, 0, 1, 1, 0))
        self.assertEqual(len([call for call in cards.remote.calls if call[0] == "PUT"]), 8)
        self.assertTrue(all("123456789" not in json.dumps(call[2]) for call in cards.remote.calls))
        self.assertEqual(len(cards.issues(chart["term_id"])), 2)  # The existing cards were published, not reissued.
        self.assertEqual((rc["seat"]["kept"], rc["seat"]["recipients"], rc["dr"]["kept"]), (7, 7, 1))
        detail = self.client.get(route).json
        self.assertEqual(len(states(detail)), 9)
        self.assertEqual(states(detail)["Example, Bob"], {**states(detail)["Example, Bob"], "kind": "seat", "state": "pending", "summary": "Pending response card", "delivery": "queued", "detail": ["Email queued to send."]})
        self.assertEqual(states(detail)["Example, Carol"]["kind"], "dr")
        self.assertEqual([seat["student"]["card"]["state"] for seat in detail["room"]["seats"] if seat["student"]], ["pending"] * 7)
        self.assertEqual(relay.sent, [])
        self.assertEqual(detail, self.read_card_by_card(route))
        # The Inbox worker sends each link once.
        while cards.mail.process_one():
            pass
        self.assertEqual(len(relay.sent), 8)
        bob_mail = next(item for item in relay.sent if item["recipient"] == "example.bob@example.test")
        self.assertEqual(bob_mail["subject"], "IMPORTANT: Information about your upcoming Test 1 for MATH 140")
        self.assertTrue(bob_mail["text_body"].startswith("Hello,\n\n"))
        self.assertNotIn("Bob", bob_mail["text_body"])
        self.assertIn("https://respond.example.test/r/", bob_mail["text_body"])
        self.assertNotIn(seat_label, bob_mail["text_body"])  # The seat is on the card, not in the email.
        detail = self.client.get(route).json
        self.assertEqual((detail["response_cards"]["seat"]["emailed"], detail["response_cards"]["dr"]["emailed"]), (7, 1))
        self.assertTrue(states(detail)["Example, Bob"]["detail"][0].startswith("Emailed "))
        # While a card is pending, the seat offers to resend its email; a second copy shows on the chart once it goes out.
        self.assertEqual({(item["can_resend"], item["link_available"]) for item in states(detail).values() if item["card_id"]}, {(True, True)})
        self.assertEqual((states(detail)["Example, Alice"]["can_resend"], states(detail)["Example, Alice"]["link_available"]), (False, False))
        # The chart payload carries no tokens; the popover fetches one card's link on demand.
        self.assertNotIn("/r/", json.dumps(detail))
        bob_link = self.client.get(f"/api/response-cards/cards/{states(detail)['Example, Bob']['card_id']}/link", headers=HEADERS).json["link"]
        self.assertTrue(bob_link.startswith("https://respond.example.test/r/"))
        bob_tile = next(seat["student"]["card"] for seat in detail["room"]["seats"] if seat["student"] and seat["student"]["name"] == "Example, Bob")
        resent = self.client.post(f"/api/response-cards/cards/{bob_tile['card_id']}/resend", json={}, headers=HEADERS)
        self.assertEqual((resent.status_code, resent.json["delivery_status"], resent.json["copies"]), (200, "queued", 2))
        queued = states(self.client.get(route).json)["Example, Bob"]
        self.assertEqual((queued["state"], queued["detail"], queued["can_resend"], queued["link_available"]), ("pending", ["Email queued to send."], False, True))
        while cards.mail.process_one():
            pass
        self.assertEqual(len(relay.sent), 9)
        self.assertIn("second copy of an earlier email", relay.sent[-1]["text_body"])
        detail = self.client.get(route).json
        self.assertTrue(states(detail)["Example, Bob"]["detail"][0].startswith("Emailed 2 times, most recently "))
        self.assertEqual(detail, self.read_card_by_card(route))
        self.assertTrue(states(detail)["Example, Bob"]["can_resend"])
        # Bob completes his card; Carol's DR date differs from her booking; both get a confirmation email.
        seat_issue = cards.get_issue(rc["seat"]["issue_id"])
        bob_card = next(card for card in seat_issue["recipients"] if card["student_name"] == "Example, Bob")
        carol_card = cards.get_issue(rc["dr"]["issue_id"])["recipients"][0]
        ok = {"matched": True, "on_mismatch": "BLOCK_SUBMISSION"}
        cards.remote.event(bob_card["id"], "verified", submission={"id": "sub-bob", "request_id": "r-bob", "status": "verified", "submitted_at": "2026-09-15T14:05:00.000Z",
            "answers": {"confirm_room": "hecc105", "confirm_seat": seat_label.lower(), "custom_1": True, "custom_2": "TI-84", "confirm_uin": "[UIN confirmed]"},
            "verification": {"confirm_room": ok, "confirm_seat": ok, "confirm_uin": ok}})
        cards.remote.event(carol_card["id"], "needs_review", submission={"id": "sub-carol", "request_id": "r-carol", "status": "needs_review", "submitted_at": "2026-09-15T15:00:00.000Z",
            "answers": {"confirm_dr_date": "2026-09-18", "confirm_dr_time": "19:05", "confirm_dr_location": "College Station", "confirm_uin": "[UIN confirmed]"},
            "verification": {"confirm_dr_date": {"matched": False, "on_mismatch": "ALLOW_AND_FLAG"}, "confirm_dr_time": {"matched": True, "on_mismatch": "ALLOW_AND_FLAG"},
                             "confirm_dr_location": {"matched": True, "on_mismatch": "ALLOW_AND_FLAG"}, "confirm_uin": ok}})
        synced = cards.sync()
        self.assertEqual((synced["events"], synced["confirmations_queued"]), (10, 2))
        detail = self.client.get(route).json
        bob_state, carol_state = states(detail)["Example, Bob"], states(detail)["Example, Carol"]
        self.assertEqual((bob_state["state"], bob_state["summary"], bob_state["detail"][-1]), ("complete", "Completed response card", "Confirmation email queued."))
        self.assertEqual((bob_state["can_resend"], carol_state["can_resend"], bob_state["link_available"]), (False, False, False))  # Responded cards are never re-sent.
        self.assertEqual((carol_state["state"], carol_state["summary"]), ("issue", "Answers need review"))
        self.assertIn("Confirm the date of your scheduled DR exam: answered “2026-09-18”; records show “2026-09-17”.", carol_state["detail"])
        # The problem lines travel separately so the table can name the mismatch, not just its heading.
        self.assertEqual(carol_state["problems"], ["The answers differ from our records:", "Confirm the date of your scheduled DR exam: answered “2026-09-18”; records show “2026-09-17”."])
        self.assertEqual((bob_state["problems"], states(detail)["Example, Alice"]["problems"]), ([], ["No DR card was prepared for this student: more than one booking is linked, or the booking came after the cards."]))
        self.assertEqual((detail["response_cards"]["dr"]["flagged"], detail["response_cards"]["seat"]["responded"]), (1, 1))
        self.assertEqual(detail, self.read_card_by_card(route))
        self.assertEqual(next(seat["student"]["card"]["state"] for seat in detail["room"]["seats"] if seat["student"] and seat["student"]["name"] == "Example, Bob"), "complete")
        while cards.mail.process_one():
            pass
        confirmations = [item for item in relay.sent if item["subject"].startswith("Confirmed:")]
        self.assertEqual(len(confirmations), 2)
        bob_confirmation = next(item for item in confirmations if item["recipient"] == "example.bob@example.test")
        self.assertEqual(bob_confirmation["subject"], "Confirmed: your responses for Test 1 (MATH 140)")
        self.assertIn(f"Your assigned seat is {seat_label}.", bob_confirmation["text_body"])
        self.assertIn(f"- Please confirm your assigned seat as shown above: {seat_label.lower()}\n- I will bring a pencil: Yes\n- Which calculator will you bring? TI-84\n- Type your UIN to confirm that you have read and understood all of the above: confirmed", bob_confirmation["text_body"])
        self.assertNotIn("123456789", bob_confirmation["text_body"])
        carol_confirmation = next(item for item in confirmations if item["recipient"] == "example.carol@example.test")
        self.assertIn("- Confirm the date of your scheduled DR exam: 2026-09-18 (differs from our records)", carol_confirmation["text_body"])
        self.assertIn("Professor Sinkule will review them", carol_confirmation["text_body"])
        self.assertNotIn("2026-09-17", carol_confirmation["text_body"])
        detail = self.client.get(route).json
        self.assertEqual(states(detail)["Example, Bob"]["detail"][-1], "Confirmation emailed.")
        # Moving Bob after his card went out flags the stale seat on the chart.
        free = next(seat for seat in detail["room"]["seats"] if not seat["student"] and not seat["broken"] and not seat["blocked"])
        moved = self.client.post(route + "/move", json={"version": detail["version"], "enrollment_id": bob_card["enrollment_id"], "seat_id": free["id"]}, headers=HEADERS).json
        bob_state = states(moved)["Example, Bob"]
        self.assertEqual(bob_state["state"], "update_pending")
        self.assertTrue(any(free['label'] in line for line in bob_state["detail"]))
        self.assertEqual(next(seat["student"]["card"]["state"] for seat in moved["room"]["seats"] if seat["id"] == free["id"]), "update_pending")
        self.assertEqual((bob_state["can_resend"], bob_state["link_available"]), (False, False))  # A stale card is replaced by re-confirming, never re-sent or handed over.
        # Confirming again replaces only Bob's card: the old one is revoked, everyone else keeps theirs, and one UPDATED email goes out.
        again = self.client.post(route + "/confirm", json={**base, "version": moved["version"], "request_id": "cards-4", "exam_date": "2026-09-25", "exam_time": "7:00 PM"}, headers=HEADERS).json
        rc4 = again["response_cards"]
        self.assertEqual((rc4["seat"]["recipients"], rc4["seat"]["kept"], rc4["seat"]["replaced"], len(rc4["seat"]["issue_ids"]), rc4["dr"]["recipients"], rc4["dr"]["kept"], rc4["errors"]),
                         (7, 6, 1, 2, 1, 1, ["Example, Alice has no single DR booking linked to Test 1, so no DR notice was prepared; notify them directly."]))
        self.assertNotIn("seats", rc4["seat"])  # The recorded seats stay private to the chart record.
        self.assertTrue(any(card_id == bob_card["id"] and action == "revoke"
                            for (card_id, _), action in cards.remote.controls.items()))
        bob_state = states(again)["Example, Bob"]
        self.assertEqual(bob_state["state"], "update_pending")
        self.assertIn("Email queued to send.", bob_state["detail"])
        self.assertNotEqual(bob_state["card_id"], bob_card["id"])
        self.assertEqual(len(cards.issues(chart["term_id"])), 3)
        while cards.mail.process_one():
            pass
        updated = [item for item in relay.sent if item["subject"].startswith("UPDATED:")]
        self.assertEqual((len(updated), updated[0]["recipient"], len(relay.sent)), (1, "example.bob@example.test", 12))
        self.assertEqual(updated[0]["subject"], "UPDATED: Information about your upcoming Test 1 for MATH 140")
        self.assertIn("this new card replaces it, and the earlier link no longer works", updated[0]["text_body"])
        final = self.client.get(route).json
        self.assertEqual((final["response_cards"]["seat"]["recipients"], final["response_cards"]["seat"]["emailed"], final["response_cards"]["seat"]["issue_id"]), (7, 7, rc4["seat"]["issue_id"]))
        self.assertTrue(states(final)["Example, Bob"]["detail"][0].startswith("Emailed "))
        self.assertEqual(final, self.read_card_by_card(route))
        # Two mail rows of one card and kind recorded at the same moment: the one recorded last counts, in the chart's read of
        # every card's mail as in a card's own read (`_card_states` orders by created_at, then rowid).
        bob_id, carol_id = states(final)["Example, Bob"]["card_id"], states(final)["Example, Carol"]["card_id"]
        tied = "2026-12-01T12:00:00Z"
        with connect(self.app.config["DATABASE"]) as db:
            for card_id, kind, submission_id, status, error, updated_at in (
                    (bob_id, "invitation", "", "accepted", "", tied), (bob_id, "invitation", "", "accepted", "", "2026-12-02T15:30:00Z"),
                    (carol_id, "confirmation", "tie-1", "failed", "C-first", tied), (carol_id, "confirmation", "tie-2", "pending", "", tied)):
                db.execute("""INSERT INTO response_card_mail(id,card_id,kind,submission_id,recipient,subject,text_body,html_body,status,error,created_at,updated_at)
                              VALUES(?,?,?,?,'student@example.test','Subject','Text','<p>Text</p>',?,?,?,?)""",
                           (str(uuid.uuid4()), card_id, kind, submission_id, status, error, tied, updated_at))
        tie = self.client.get(route).json
        self.assertEqual(tie, self.read_card_by_card(route))
        self.assertEqual(states(tie)["Example, Bob"]["detail"][0], f"Emailed 3 times, most recently {recorded_time('2026-12-02T15:30:00Z')}.")
        carol_state = states(tie)["Example, Carol"]
        self.assertIn("Confirmation email queued.", carol_state["detail"])
        self.assertFalse(any("C-first" in line for line in carol_state["detail"]))
        # RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › THEY KEEP THEIR STATUS (requirement, 2026-10-23): from midnight after the
        # exam each card shows what it was when it closed, so the chart still says who responded; a card revoked earlier stays red.
        from datetime import datetime, timezone
        cards.remote.event(bob_id, "verified", submission={"id": "sub-bob-2", "request_id": "r-bob-2", "status": "verified", "submitted_at": "2026-09-20T14:05:00.000Z",
            "answers": {"confirm_uin": "[UIN confirmed]"}, "verification": {"confirm_uin": ok}})
        early = next(name for name, item in states(tie).items() if item["kind"] == "seat" and item["card_id"] and name != "Example, Bob")
        cards.remote.event(states(tie)[early]["card_id"], "revoked")
        cards.sync()
        before = self.client.get(route).json
        self.assertEqual((states(before)["Example, Bob"]["state"], states(before)[early]["state"], states(before)[early]["problems"]),
                         ("complete", "issue", ["The card was revoked."]))
        listed = lambda: next(item for item in self.client.get("/api/state").json["seating_charts"] if item["id"] == chart["id"])
        listed_before = listed()
        closed = cards.close_finished_exams(datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((closed["closed"] > 0, closed["errors"]), (True, []))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM response_card_recipients WHERE status='revoked' AND id=?", (bob_id,)).fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT 1 FROM response_card_invalidations WHERE card_id=?", (states(tie)[early]["card_id"],)).fetchone())
        after = self.client.get(route).json
        self.assertEqual(after, self.read_card_by_card(route))
        bob_after, early_after, carol_after = states(after)["Example, Bob"], states(after)[early], states(after)["Example, Carol"]
        self.assertEqual((bob_after["state"], bob_after["summary"], bob_after["detail"][-1]), ("complete", "Completed response card", "The card closed at midnight after the exam."))
        self.assertEqual((early_after["state"], early_after["problems"]), ("issue", ["The card was revoked."]))
        self.assertEqual((carol_after["state"], carol_after["summary"], carol_after["problems"]), ("issue", "Answers need review", states(before)["Example, Carol"]["problems"]))
        unanswered = [item for name, item in states(after).items() if item["kind"] == "seat" and item["card_id"] and name not in ("Example, Bob", early)]
        self.assertTrue(unanswered)
        self.assertEqual({(item["state"], item["summary"], item["detail"][-1], item["can_resend"], item["link_available"]) for item in unanswered},
                         {("pending", "No response", "No response before the card closed at midnight after the exam.", False, False)})
        self.assertEqual({seat["student"]["card"]["state"] for seat in after["room"]["seats"] if seat["student"] and seat["student"].get("card")}, {"complete", "pending", "issue"})
        for kind in ("seat", "dr"):
            self.assertEqual({key: after["response_cards"][kind][key] for key in ("recipients", "responded", "flagged")},
                             {key: before["response_cards"][kind][key] for key in ("recipients", "responded", "flagged")})
        self.assertEqual((listed()["card_counts"], listed()["seat_updates_pending"]), (listed_before["card_counts"], listed_before["seat_updates_pending"]))

    def test_deletion_needs_typed_confirmation_once_print_jobs_exist(self):
        chart = self.create().json
        self.assertEqual((chart["print_jobs"], chart["delete_confirmation"]), (0, "yes_no"))
        self.assertEqual(self.client.delete(f"/api/seating/{chart['id']}", json={"version": chart["version"]}, headers=HEADERS).status_code, 204)
        chart = self.create().json
        version = self.client.post("/api/documents/versions/fixture", json={"assignment_id": self.assignment["id"], "label": "A", "paper": "letter"}, headers=HEADERS).json
        batch = self.client.post("/api/documents/batches", json={"version_id": version["id"], "section_id": self.sections[507]["id"], "quantity": 2, "request_id": "seating-delete-1"}, headers=HEADERS)
        self.assertEqual(batch.status_code, 202, batch.json)
        armed = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual((armed["print_jobs"], armed["delete_confirmation"]), (1, "typed"))
        self.assertEqual(self.client.get("/api/state").json["seating_charts"][0]["delete_confirmation"], "typed")
        for confirmation in (None, "", "yes", "delet"):
            with self.subTest(confirmation=confirmation):
                refused = self.client.delete(f"/api/seating/{chart['id']}", json={"version": armed["version"], "confirmation": confirmation}, headers=HEADERS)
                self.assertEqual(refused.status_code, 409, refused.json)
                self.assertEqual(refused.json["error"], "1 print job with Data Matrix page stamps already exists for Test 1 this term. Type delete to confirm removing this seating chart.")
        self.assertEqual(self.client.get(f"/api/seating/{chart['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/seating/{chart['id']}", json={"version": armed["version"], "confirmation": " Delete "}, headers=HEADERS).status_code, 204)
        # A cancelled print job no longer counts; another section's job for the same exam still does.
        cancelled = self.client.post(f"/api/documents/batches/{batch.json['id']}/cancel", json={}, headers=HEADERS)
        self.assertEqual(cancelled.status_code, 200, cancelled.json)
        chart = self.create().json
        self.assertEqual(chart["delete_confirmation"], "yes_no")
        other = self.client.post("/api/documents/batches", json={"version_id": version["id"], "section_id": self.sections[508]["id"], "quantity": 1, "request_id": "seating-delete-2"}, headers=HEADERS)
        self.assertEqual(other.status_code, 202, other.json)
        only_507 = self.create(assignment_id=self.quiz["id"], section_ids=[self.sections[507]["id"]]).json
        self.assertEqual((self.client.get(f"/api/seating/{chart['id']}").json["delete_confirmation"], only_507["delete_confirmation"]), ("typed", "yes_no"))

    def test_moves_swap_and_respect_conditions_and_dr_bookings(self):
        chart = self.create().json
        students = self.by_name(chart)
        seats = {seat["label"]: seat for seat in chart["room"]["seats"]}
        free = next(seat for seat in chart["room"]["seats"] if not seat["student"] and not seat["broken"] and not seat["blocked"])
        route = f"/api/seating/{chart['id']}/move"
        moved = self.client.post(route, json={"version": chart["version"], "enrollment_id": students["Example, Bob"]["enrollment_id"], "seat_id": free["id"]}, headers=HEADERS).json
        self.assertEqual(self.by_name(moved)["Example, Bob"]["seat"], free["label"])
        target = self.by_name(moved)["Student, 4"]
        swapped = self.client.post(route, json={"version": moved["version"], "enrollment_id": students["Example, Bob"]["enrollment_id"], "seat_id": seats[target["seat"]]["id"]}, headers=HEADERS).json
        self.assertEqual(self.by_name(swapped)["Example, Bob"]["seat"], target["seat"])
        self.assertEqual(self.by_name(swapped)["Student, 4"]["seat"], free["label"])
        self.assertEqual(swapped["events"][0]["detail"]["swapped_with"], target["enrollment_id"])
        room = self.client.get(f"/api/rooms/{self.hall['id']}").json
        broken_seat = next(seat for seat in chart["room"]["seats"] if not seat["student"] and seat["id"] != free["id"])
        self.client.post(f"/api/rooms/{self.hall['id']}/seats", json={"version": room["version"], "seat_ids": [broken_seat["id"]], "broken": True}, headers=HEADERS)
        rejected = self.client.post(route, json={"version": swapped["version"], "enrollment_id": students["Example, Bob"]["enrollment_id"], "seat_id": broken_seat["id"]}, headers=HEADERS)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("is broken", rejected.json["error"])
        dr = self.client.post(route, json={"version": swapped["version"], "enrollment_id": students["Example, Alice"]["enrollment_id"], "seat_id": free["id"]}, headers=HEADERS)
        self.assertEqual(dr.status_code, 409)
        self.assertIn("DR exam booking", dr.json["error"])
        self.assertEqual(self.client.post(route, json={"version": 1, "enrollment_id": "x", "seat_id": free["id"]}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.post(route, json={"version": swapped["version"], "enrollment_id": "nobody", "seat_id": free["id"]}, headers=HEADERS).status_code, 400)

    def test_swap_never_moves_the_other_student_into_an_unusable_seat(self):
        # SEATING: both destinations of a swap must be usable, including the mover's old seat.
        chart = self.create().json
        seated = [student for student in chart['students'] if student['status'] == 'seated']
        source, other = seated[:2]
        self.app.extensions['rooms'].set_conditions(self.hall['id'], self.hall['version'], [source['seat_id']], {'blocked': True})
        result = self.client.post(f"/api/seating/{chart['id']}/move", json={
            'version': chart['version'], 'enrollment_id': source['enrollment_id'], 'seat_id': other['seat_id']}, headers=HEADERS)
        self.assertEqual(result.status_code, 409, result.json)
        after = self.client.get(f"/api/seating/{chart['id']}").json
        by_id = {student['enrollment_id']: student['seat_id'] for student in after['students']}
        self.assertEqual(by_id[source['enrollment_id']], source['seat_id'])
        self.assertEqual(by_id[other['enrollment_id']], other['seat_id'])
        self.assertEqual(after['version'], chart['version'])

    def test_plan_rejects_nested_section_ids_before_deduplication(self):
        response = self.client.post('/api/seating/plan', json={
            'assignment_id': self.assignment['id'], 'allocations': [{'room_id': self.hall['id'], 'section_ids': [{}]}]}, headers=HEADERS)
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get('/api/seating').json, [])

    def test_exam_name_matching_and_relinking_other_exams(self):
        # "Exam 1" bookings link to the "Test 1" assignment automatically.
        self.import_dr([booking(UIN="000000003", **{"Last Name": "Student", "First Name": "3", "Exam Type": "Exam 1"})])
        chart = self.create().json
        self.assertEqual((chart["counts"]["excluded_dr"], chart["counts"]["seated"]), (3, 6))
        self.assertEqual(self.by_name(chart)["Student, 3"]["status"], "dr_testing")
        # A booking named "Midterm 1" created its own assignment; relink it to Test 1 from chart settings.
        self.import_dr([booking(UIN="000000004", **{"Last Name": "Student", "First Name": "4", "Exam Type": "Midterm 1"})])
        pending = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(pending["issues"], [])
        self.assertEqual(len(pending["notices"]), 2)
        self.assertIn("DR bookings linked to Midterm 1 (created from the DR export), not to Test 1", "".join(pending["notices"]))
        midterm = next(item for item in pending["dr"]["others"] if item["title"] == "Midterm 1")
        changed = self.client.patch(f"/api/seating/{chart['id']}", json={"version": pending["version"], "relink_assignment_ids": [midterm["assignment_id"]], "notes": "Room B"}, headers=HEADERS).json
        self.assertEqual((changed["notes"], changed["events"][0]["kind"], changed["events"][0]["detail"]["moved"][0]["bookings"]), ("Room B", "relink", 1))
        self.assertEqual(changed["issues"], [f"Student, 4 has a DR exam booking for Test 1 but is seated at {self.by_name(chart)['Student, 4']['seat']}."])
        self.assertEqual([item["title"] for item in changed["dr"]["others"]], ["Final"])
        updated = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": changed["version"]}, headers=HEADERS).json
        self.assertEqual((updated["counts"]["seated"], updated["counts"]["excluded_dr"], updated["issues"]), (5, 4, []))
        self.assertEqual(self.client.patch(f"/api/seating/{chart['id']}", json={"version": chart["version"], "notes": "old"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.patch(f"/api/seating/{chart['id']}", json={"version": updated["version"], "relink_assignment_ids": [self.quiz["id"], "x"]}, headers=HEADERS).status_code, 400)
        # Creating a chart can relink in the same step.
        physics_free = self.create(assignment_id=self.quiz["id"], room_id=self.hall["id"], relink_assignment_ids=[self.assignment["id"]]).json
        self.assertEqual((physics_free["counts"]["excluded_dr"], physics_free["events"][-1]["kind"]), (4, "relink"))

    def test_attachable_plans_check_sections_and_the_exam_name(self):
        both = f"{self.sections[507]['id']},{self.sections[508]['id']}"
        query = f"/api/seating/attachable?definition_id={self.definition_id}"
        self.assertEqual(self.client.get(f"{query}&sections={both}&title=Test%201").json["plans"], [])
        # The DR export created "Final"; its chart seats Section 507 only.
        final = next(item for item in self.client.get("/api/seating/catalog").json["assignments"] if item["title"] == "Final")
        chart = self.create(assignment_id=final["id"], section_ids=[self.sections[507]["id"]]).json
        plan, = self.client.get(f"{query}&sections={both}&title=Final%20Exam").json["plans"]
        self.assertEqual((plan["assignment_id"], plan["title"], plan["covered"], plan["extra"], plan["uncovered"], plan["uncovered_ids"]),
                         (final["id"], "Final", [507], [], [508], [self.sections[508]["id"]]))
        self.assertEqual((plan["compatible"], plan["name_conflict"]), (True, True))
        self.assertEqual([(item["id"], item["room_name"], item["sections"], item["seated_count"]) for item in plan["charts"]],
                         [(chart["id"], "HECC 105", [507], chart["counts"]["seated"])])
        # Spacing and capitalization are not a name conflict; a chart seating an unchosen section is incompatible.
        self.assertFalse(self.client.get(f"{query}&sections={both}&title=%20final").json["plans"][0]["name_conflict"])
        narrow, = self.client.get(f"{query}&sections={self.sections[508]['id']}&title=Final").json["plans"]
        self.assertEqual((narrow["compatible"], narrow["extra"], narrow["covered"], narrow["uncovered"]), (False, [507], [], [508]))
        for bad in (f"/api/seating/attachable?definition_id=missing&sections={both}", f"{query}&sections=", f"{query}&sections=x"):
            self.assertEqual(self.client.get(bad).status_code, 400, bad)

        # The typed exam is its own retained assignment; the unconfirmed chart moves to it and Alice's Final booking follows.
        typed = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "final-exam", "title": "Final Exam"}, headers=HEADERS).json

        def attach(**overrides):
            return self.client.post("/api/seating/attach", json={"assignment_id": typed["id"], "chart_ids": [chart["id"]], **overrides}, headers=HEADERS)
        self.assertEqual(attach(chart_ids=[]).status_code, 400)
        self.assertEqual(attach(chart_ids=[chart["id"], chart["id"]]).status_code, 400)
        self.assertEqual(attach(assignment_id="missing").status_code, 404)
        self.assertEqual(attach(chart_ids=["missing"]).status_code, 404)
        taken = self.create(section_ids=[self.sections[507]["id"]]).json
        overlap = attach(assignment_id=self.assignment["id"])
        self.assertEqual(overlap.status_code, 409, overlap.json)
        self.assertIn("Already seated for Test 1", overlap.json["error"])
        self.assertEqual(self.client.delete(f"/api/seating/{taken['id']}", json={"version": taken["version"]}, headers=HEADERS).status_code, 204)
        moved = attach()
        self.assertEqual(moved.status_code, 200, moved.json)
        live, = moved.json["charts"]
        self.assertEqual((moved.json["relinked_bookings"], live["title"], live["assignment_id"], live["version"], live["issues"]),
                         (1, "Final Exam", typed["id"], chart["version"] + 1, []))
        self.assertEqual((live["events"][0]["kind"], live["events"][0]["detail"]["from_title"], live["events"][0]["detail"]["to_title"]),
                         ("exam_changed", "Final", "Final Exam"))
        self.assertEqual(self.by_name(live)["Example, Alice"]["status"], "dr_testing")
        self.assertEqual(attach().json["relinked_bookings"], 0)
        again, = self.client.get(f"{query}&sections={both}&title=Final%20Exam").json["plans"]
        self.assertEqual((again["assignment_id"], again["name_conflict"], again["rename_blockers"]), (typed["id"], False, []))
        # The assignment created from the DR export is retained under its own name.
        self.assertIn("Final", [item["title"] for item in self.client.get("/api/seating/catalog").json["assignments"]])
        # A later DR export that still says "Final" links there again; chart settings offer the relink.
        self.import_dr([booking(UIN="000000005", **{"Last Name": "Student", "First Name": "5", "Exam Type": "Final"})])
        later = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual([(item["title"], item["students"]) for item in later["dr"]["others"] if item["assignment_id"] == final["id"]], [("Final", 1)])
        # Paper, cards, email or exam records carry the chart's exam name, so a confirmed chart stays where it is.
        confirmed = self.client.post(f"/api/seating/{chart['id']}/confirm", json={"version": later["version"], "mode": "stamps_only", "page_count": 1,
                                     "request_id": "attach-confirm", "prepare_cards": False, "acknowledge_issues": True}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        back = attach(assignment_id=final["id"])
        self.assertEqual(back.status_code, 409, back.json)
        self.assertIn("keeps the name Final Exam: it is confirmed for printing; print jobs were created from it", back.json["error"])
        blocked, = self.client.get(f"{query}&sections={both}&title=Final").json["plans"]
        self.assertEqual(blocked["rename_blockers"], ["HECC 105: it is confirmed for printing", "HECC 105: print jobs were created from it"])



class SeatingListSeatUpdateTests(unittest.TestCase):
    """GET /api/seating counts the updated seat cards still to send per chart, the count the sidebar badge and the chart's
    'Seat update not sent' tiles show (read-only, count only). Uses the seat-update fixture (confirmed chart, emailed cards)."""

    def __getattr__(self, name):
        # Borrow the fixture's helpers lazily (test_seating_updates imports this module through its own fixture).
        import test_seating_updates
        value = getattr(test_seating_updates.SeatingUpdateTests, name, None)
        if value is None or not callable(value):
            raise AttributeError(name)
        return value.__get__(self)

    def setUp(self):
        import test_seating_updates
        test_seating_updates.SeatingUpdateTests.setUp(self)

    def row(self):
        return next(item for item in self.client.get("/api/seating").json if item["id"] == self.chart["id"])

    def test_list_rows_count_seat_updates_until_the_new_card_is_accepted(self):
        self.assertEqual(self.row()["seat_updates_pending"], 0)
        self.move()
        self.assertEqual(self.row()["seat_updates_pending"], 1)
        self.assertEqual(self.current()["seat_updates"]["pending"], 1)
        self.send_updates()
        self.drain()
        self.assertEqual(self.row()["seat_updates_pending"], 0)
        self.assertEqual(self.current()["seat_updates"]["pending"], 0)


if __name__ == "__main__":
    unittest.main()
