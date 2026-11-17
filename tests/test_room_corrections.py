"""The reviewed room corrections (AGENTS.md "Rooms and seating charts"): preview, one-time apply, seat identity."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest import mock

from openpyxl.utils import get_column_letter

import manage_rooms
from app import create_app
from room_corrections import ACCESSIBLE, CORRECTIONS, KEPT_FREE, PLAN_WHEELCHAIR, REPEATED, WHEELCHAIR
from room_pdf_import import parse_pdf_chart, source_index
from room_sources import apply_issues
from rooms import CORRECTIONS_LOG, RoomService
from tests.test_rooms import chart_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
HECC_ROW_G = {"A1": "H", "B1": 1, "C1": 2, "A2": "G", "F2": 8, "G2": 7, "H2": 6, "I2": 5, "J2": 4, "K2": 6, "L2": 2, "M2": 1}
HECC_204 = {"G2": 53, "H2": 54, "J2": 55, "K2": 56, "G7": 56, "H7": 57}


def ilsb_cells():
    """Fourteen unlettered rows, each numbered 1… in a left and a right section; the front row is the last one."""
    cells = {"A1": "Long tables/no #'s"}
    for row in range(2, 15):
        cells.update({f"B{row}": 1, f"C{row}": 2, f"E{row}": 1, f"F{row}": 2})
    cells.update({f"{get_column_letter(1 + number)}15": number for number in range(1, 8)})
    cells.update({"I15": "8H (moveable chair)", "K15": "1 H (moveable chair)", "L15": 2})
    return cells


class RoomCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.rooms = self.app.extensions["rooms"]

    def add(self, name, cells):
        added = self.rooms.import_many([{"name": name, "filename": f"{name}.xlsx", "content": chart_bytes(cells)}])["added"][0]
        return self.rooms.get(added["id"])

    def add_plan(self, name, issues=()):
        """A reviewed ILCB plan as the department's PDF and seat-issue notes import it."""
        if not source_index():
            self.skipTest("the public copy leaves out the reviewed ILCB plans")
        digest = next(key for key, value in source_index().items() if value["room"] == name)
        with mock.patch("room_pdf_import.hashlib.sha256") as sha256:
            sha256.return_value.hexdigest.return_value = digest
            chart = parse_pdf_chart(b"plan", f"{name}.pdf")
        if issues:
            apply_issues(chart, {"filename": f"{name} Seat Issues.docx", "sha256": "notes", "paragraphs": [f"{name} Seat Issues", *issues]}, name)
        with mock.patch.object(RoomService, "parse_chart", staticmethod(lambda *args, **kwargs: chart)):
            added = self.rooms.import_many([{"name": name, "filename": f"{name}.pdf", "content": b"plan"}])["added"][0]
        return self.rooms.get(added["id"])

    def seats(self, room):
        return {seat["label"]: seat for seat in self.rooms.get(room["id"])["seats"]}

    def corrected(self, result, name):
        return next(item for item in result["corrected"] if item["name"] == name)

    def test_preview_writes_nothing_and_apply_corrects_a_plan_once(self):
        room = self.add_plan("ILCB 111", ["Seat 404 on map is actually 405", "Seat 405 on the map is actually 406"])
        before = {seat["label"]: seat for seat in room["seats"]}
        accessible = sorted(label for label, seat in before.items() if seat["notes"].startswith(PLAN_WHEELCHAIR))
        self.assertEqual(accessible, ["105C", "106C", "203A", "305C", "306C", "403A"])
        self.assertEqual(len(room["review_required"]), 2)

        preview = self.rooms.apply_corrections()
        self.assertFalse(preview["applied"])
        self.assertEqual(self.rooms.get(room["id"]), room)
        item = self.corrected(preview, "ILCB 111")
        self.assertEqual((item["kept_free"], item["relabelled"], item["review_settled"], item["review_left"]), (accessible, 2, 2, []))
        self.assertEqual(sorted(preview["missing"]), sorted(set(CORRECTIONS) - {"ILCB 111"}))

        applied = self.rooms.apply_corrections(apply=True)
        self.assertEqual(self.corrected(applied, "ILCB 111"), item)
        after = self.rooms.get(room["id"])
        seats = {seat["label"]: seat for seat in after["seats"]}
        # Row A's map seats 404 and 405 are the same physical seats under their real numbers.
        self.assertNotIn("404A", seats)
        self.assertEqual((seats["405A"]["id"], seats["405A"]["number"], seats["405A"]["display"]), (before["404A"]["id"], 405, "405"))
        self.assertEqual((seats["406A"]["id"], seats["406A"]["number"]), (before["405A"]["id"], 406))
        self.assertIn("Map seat 404A", seats["405A"]["notes"])
        self.assertEqual({seat["id"] for seat in after["seats"]}, {seat["id"] for seat in room["seats"]})
        self.assertEqual(set(after["layout"]["seats"]), set(seats))
        self.assertEqual(after["layout"]["seats"]["406A"], room["layout"]["seats"]["405A"])
        for label in accessible:
            self.assertTrue(seats[label]["blocked"])
            self.assertEqual(seats[label]["notes"], f"{PLAN_WHEELCHAIR} {KEPT_FREE}")
        self.assertEqual((after["review_required"], after["blocked_count"], after["usable_count"]), ([], 6, room["usable_count"] - 6))
        self.assertEqual(after["version"], room["version"] + 1)
        receipt = after["imports"][0]
        self.assertEqual((receipt["filename"], receipt["seat_count"], receipt["kept"], receipt["added"], receipt["removed"]),
                         (CORRECTIONS_LOG, 356, 356, 0, 0))
        self.assertIn("Relabelled 404A → 405A, 405A → 406A; same physical seats and IDs.", receipt["warnings"])
        self.assertTrue(any(note.endswith("Seat 404 on map is actually 405") for note in receipt["warnings"]))

        # A seat the instructor frees for a student stays free: a room is corrected once.
        self.rooms.set_conditions(room["id"], after["version"], [seats["105C"]["id"]], {"blocked": False})
        again = self.rooms.apply_corrections(apply=True)
        self.assertEqual((again["corrected"], again["already"]), ([], ["ILCB 111"]))
        self.assertFalse(self.seats(room)["105C"]["blocked"])

    def test_plan_notes_become_room_notes_and_a_missing_chair_is_unusable(self):
        short = self.add_plan("ILCB 207", ["8 chairs short in the room"])
        fan = self.add_plan("ILCB 226", ["Only 7 chairs at the table in row E"])
        tables = self.add_plan("ILCB 233")
        self.assertTrue(all(room["review_required"] for room in (short, fan, tables)))
        self.rooms.apply_corrections(apply=True)
        short, fan, tables = (self.rooms.get(room["id"]) for room in (short, fan, tables))
        self.assertEqual([room["review_required"] for room in (short, fan, tables)], [[], [], []])
        self.assertIn("8 chairs short in the room, location not recorded", short["notes"])
        self.assertIn("Students can sit at any chair of their table.", tables["notes"])
        seat = next(seat for seat in fan["seats"] if seat["label"] == "16E")
        self.assertEqual((seat["broken"], seat["notes"]), (True, "The row E table has only 7 chairs (DR seat-issue notes); this position is left out."))
        self.assertEqual((short["blocked_count"], fan["blocked_count"], tables["blocked_count"]), (6, 5, 4))

    def test_workbook_typos_are_renumbered_and_repeated_numbers_kept_unassigned(self):
        typo = self.add("HECC 200", HECC_ROW_G)
        repeated = self.add("HECC 204", HECC_204)
        before = {seat["label"]: seat for seat in typo["seats"]}
        self.assertEqual(len(typo["review_required"]), 1)
        result = self.rooms.apply_corrections(apply=True)
        self.assertEqual((self.corrected(result, "HECC 200")["relabelled"], self.corrected(result, "HECC 204")["kept_free"]), (2, ["56 · G7", "56 · K2"]))
        seats = self.seats(typo)
        self.assertEqual([seat["label"] for seat in self.rooms.get(typo["id"])["seats"]], ["H1", "H2", "G8", "G7", "G6", "G5", "G4", "G3", "G2", "G1"])
        self.assertEqual((seats["G6"]["id"], seats["G6"]["notes"]), (before["G6 · H2"]["id"], ""))
        self.assertEqual((seats["G3"]["id"], seats["G3"]["number"], seats["G3"]["display"]), (before["G6 · K2"]["id"], 3, "3"))
        self.assertIn("cell K2", seats["G3"]["notes"])
        self.assertEqual(self.rooms.get(typo["id"])["review_required"], [])
        seats = self.seats(repeated)
        for label in ("56 · K2", "56 · G7"):
            self.assertEqual((seats[label]["blocked"], seats[label]["notes"]), (True, REPEATED.format(number="56", missing="")))
        self.assertFalse(seats["55"]["blocked"])
        self.assertEqual(self.rooms.get(repeated["id"])["review_required"], [])

    def test_unlettered_rows_are_lettered_from_the_front(self):
        room = self.add("ILSB 1105", ilsb_cells())
        before = {seat["label"]: seat for seat in room["seats"]}
        mark = ACCESSIBLE.format(mark="H (moveable chair)")
        # The chart reader already keeps the two "H (moveable chair)" seats free; the correction does not repeat the note.
        self.assertEqual((before["8"]["blocked"], before["8"]["notes"], before["1 · K15"]["notes"]), (True, mark, mark))
        result = self.rooms.apply_corrections(apply=True)
        self.assertEqual((self.corrected(result, "ILSB 1105")["relabelled"], self.corrected(result, "ILSB 1105")["kept_free"]), (62, []))
        after = self.rooms.get(room["id"])
        seats = {seat["label"]: seat for seat in after["seats"]}
        self.assertEqual(len(seats), 62)
        self.assertEqual((seats["A-L8"]["id"], seats["A-L8"]["blocked"], seats["A-L8"]["notes"]), (before["8"]["id"], True, mark))
        self.assertEqual((seats["A-R1"]["id"], seats["A-R1"]["notes"]), (before["1 · K15"]["id"], mark))
        self.assertEqual((seats["N-L1"]["id"], seats["N-R2"]["id"]), (before["1 · B2"]["id"], before["2 · F2"]["id"]))
        self.assertEqual((seats["N-L1"]["row_label"], seats["N-L1"]["column"], seats["N-L1"]["display"]), ("N", before["1 · B2"]["column"] + 1, "1"))
        self.assertEqual(after["grid_columns"], room["grid_columns"] + 1)
        letters = [(item["text"], item["row"], item["column"]) for item in after["landmarks"] if item["kind"] == "row"]
        self.assertEqual(sorted(letters), sorted((letter, row, 1) for letter, row in zip("ABCDEFGHIJKLMN", range(15, 1, -1))))
        caption = next(item for item in after["landmarks"] if item["kind"] == "caption")
        self.assertEqual(caption["column"], room["landmarks"][0]["column"] + 1)
        self.assertEqual(after["review_required"], [])
        self.assertIn("rows are lettered A–N from the front", after["notes"])

    def test_marks_the_reader_once_missed_are_settled_without_repeating_notes(self):
        accessible = self.add("HELD 107", {"A1": "H", "B1": 10, "C1": "11 H", "A2": "G", "B2": 10})
        wheelchair = self.add("KLCT 115", {"A1": "N", "B1": "1 Wheelchair Only", "C1": 2,
                                           "A2": "A", "B2": "4 Wheelchair Only", "C2": "5 Wheelchair Only", "D2": 6,
                                           "J2": "12 Wheelchair Only", "K2": "13 Wheelchair Only"})
        missed = "Cell C1 note “H” was not understood; seat H11 was imported without it."
        with self.rooms_db() as db:
            # HELD 107 as the reader saved it before it understood the mark.
            db.execute("UPDATE room_seats SET blocked = 0, notes = '' WHERE room_id = ?", (accessible["id"],))
            db.execute("UPDATE rooms SET review_required = ? WHERE id = ?", (json.dumps([missed]), accessible["id"]))
        result = self.rooms.apply_corrections(apply=True)
        self.assertEqual((self.corrected(result, "HELD 107")["kept_free"], self.corrected(result, "HELD 107")["review_settled"]), (["H11"], 1))
        self.assertEqual(self.corrected(result, "KLCT 115")["kept_free"], [])
        seat = self.seats(accessible)["H11"]
        self.assertEqual((seat["blocked"], seat["notes"]), (True, ACCESSIBLE.format(mark="H")))
        self.assertEqual(self.rooms.get(accessible["id"])["review_required"], [])
        seats = self.seats(wheelchair)
        self.assertEqual({label: seat["notes"] for label, seat in seats.items() if seat["blocked"]},
                         {label: WHEELCHAIR for label in ("N1", "A4", "A5", "A12", "A13")})

    def rooms_db(self):
        from rosters import connect
        return connect(self.app.config["DATABASE"])

    def test_a_room_in_use_or_not_as_imported_is_left_alone(self):
        client = self.app.test_client()
        response = client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [1]}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        state = client.get("/api/state").json
        response = client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/1/roster",
                               data={"file": (io.BytesIO(b"UIN,Name\n001234567,Example Student\n"), "roster.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        assignment = client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "exam", "title": "Exam"}, headers=HEADERS).json
        used = self.add("HECC 204", HECC_204)
        used = self.rooms.update(used["id"], used["version"], {"resolve_review": True})
        chart = client.post("/api/seating", json={"assignment_id": assignment["id"], "section_ids": [client.get("/api/seating/catalog").json["sections"][0]["id"]],
                                                  "room_id": used["id"]}, headers=HEADERS)
        self.assertEqual(chart.status_code, 201, chart.json)
        odd = self.add("KLCT 115", {"A1": "A", "B1": 4, "C1": 5})
        used, odd = self.rooms.get(used["id"]), self.rooms.get(odd["id"])
        result = self.rooms.apply_corrections(apply=True)
        self.assertEqual(result["corrected"], [])
        self.assertEqual(result["skipped"], [{"name": "HECC 204", "reason": "A seating chart uses this room."},
                                             {"name": "KLCT 115", "reason": "KLCT 115: no seat 'N1'"}])
        self.assertEqual((self.rooms.get(used["id"]), self.rooms.get(odd["id"])), (used, odd))
        self.assertNotIn(CORRECTIONS_LOG, [item["filename"] for room in (used, odd) for item in room["imports"]])

    def test_the_command_shows_the_corrections_before_it_saves_them(self):
        room = self.add("HECC 203", HECC_ROW_G)
        database = str(self.app.config["DATABASE"])

        def run(*arguments):
            output = io.StringIO()
            with mock.patch.object(sys, "argv", ["manage_rooms.py", *arguments]), contextlib.redirect_stdout(output):
                manage_rooms.main()
            return json.loads(output.getvalue())

        shown = run("--corrections", "--database", database)
        self.assertEqual((shown["applied"], [item["name"] for item in shown["corrected"]]), (False, ["HECC 203"]))
        self.assertIn("G6 · K2", self.seats(room))
        saved = run("--corrections", "--apply", "--database", database)
        self.assertEqual((saved["applied"], saved["corrected"]), (True, shown["corrected"]))
        self.assertIn("G3", self.seats(room))
        self.assertEqual(run("--corrections", "--apply", "--database", database)["already"], ["HECC 203"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run("--database", database)


if __name__ == "__main__":
    unittest.main()
