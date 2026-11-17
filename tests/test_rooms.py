"""Rooms: seating-chart parsing, seat conditions, chart replacement, and API guards."""
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from app import create_app
from room_import import parse_seating_chart
from rooms import initialize_rooms

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rooms"

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
CHART = {"B1": "Front of room", "A3": "A1", "B3": "A2", "D3": "A3", "A4": "B1", "B4": "B2", "D4": "B3"}


def chart_bytes(cells=CHART, sheet="Room", extra_sheets=(), merges=()):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for reference, value in cells.items():
        worksheet[reference] = value
    for reference in merges:
        worksheet.merge_cells(reference)
    for name in extra_sheets:
        workbook.create_sheet(name)["A1"] = "Z9"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def department_chart(name):
    """The department's own workbook, for the tests of its quirks; the public copy leaves these out."""
    path = FIXTURES / f"{name}.xlsx"
    if not path.exists():
        raise unittest.SkipTest(f"the department's seating chart {path.name} is not in this copy")
    return path.read_bytes()


def invented_hall(name):
    """A made-up lecture hall: a front caption, then rows A-L (no I), each a row letter and fourteen numbered seats in
    two banks of seven."""
    cells = {"I1": f"{name} (FRONT OF THE ROOM)"}
    for row, letter in enumerate("ABCDEFGHJKL", start=3):
        cells[f"A{row}"] = letter
        for seat in range(1, 15):
            column = seat + 1 if seat <= 7 else seat + 2  # an aisle between the banks
            cells[f"{chr(64 + column)}{row}"] = str(seat)
    return chart_bytes(cells)


def fixture(name):
    """A room workbook for tests that need any real-sized room: the department's, or an invented hall without it."""
    path = FIXTURES / f"{name}.xlsx"
    return path.read_bytes() if path.exists() else invented_hall(name)


def labels(chart):
    return [seat["label"] for seat in chart["seats"]]


def by_label(chart):
    return {seat["label"]: seat for seat in chart["seats"]}


class ParserTests(unittest.TestCase):
    def test_merged_cell_extents_count_towards_the_chart_bounds(self):
        from room_import import build_chart
        # ROOMS: bounds apply before iterating a merged row band's declared extent.
        cells = [(1, 1, 'A'), (1, 2, '1'), (2, 1, 'B'), (2, 2, '2')]
        for spans in ({(1, 1): (1000000, 1)}, {(1, 2): (1, 10000)}):
            with self.subTest(spans=spans), self.assertRaisesRegex(ValueError, 'chart spans'):
                build_chart('Room', cells, spans)

    def test_labelled_cells_without_row_letters(self):
        chart = parse_seating_chart(chart_bytes(), "room.xlsx")
        self.assertEqual(chart["sheet"], "Room")
        self.assertEqual((chart["rows"], chart["columns"]), (4, 4))
        caption = chart["landmarks"][0]
        self.assertEqual((caption["row"], caption["column"], caption["text"], caption["kind"]), (1, 2, "Front of room", "caption"))
        self.assertEqual(labels(chart), ["A1", "A2", "A3", "B1", "B2", "B3"])
        seat = by_label(chart)["A3"]
        self.assertEqual((seat["row"], seat["column"], seat["display"], seat["group"], seat["number"]), (3, 4, "A3", "", None))
        self.assertEqual(chart["warnings"], [])

    def test_offset_charts_numbers_and_whitespace_normalize(self):
        content = chart_bytes({"C5": 12.0, "D5": "  13  ", "C6": " Row\n 2 ", "E6": 14}, sheet="  Hall  2 ", extra_sheets=("Legend",))
        chart = parse_seating_chart(content, "hall.XLSX")
        self.assertEqual(chart["sheet"], "Hall 2")
        self.assertEqual((chart["rows"], chart["columns"]), (2, 3))
        self.assertEqual([(seat["row"], seat["column"], seat["label"]) for seat in chart["seats"]],
                         [(1, 1, "12"), (1, 2, "13"), (2, 1, "Row 2"), (2, 3, "14")])
        self.assertEqual(by_label(chart)["12"]["number"], 12)

    def test_department_chart_hecc_105_tables_and_mirrored_row_letters(self):
        chart = parse_seating_chart(department_chart("HECC 105"), "HECC 105.xlsx")
        seats = by_label(chart)
        self.assertEqual(len(seats), 154)
        self.assertEqual((chart["rows"], chart["columns"]), (10, 17))
        self.assertEqual([item["text"] for item in chart["landmarks"] if item["kind"] == "row"], list("KJLGFEDCBA"))
        self.assertEqual(sum(1 for item in chart["landmarks"] if item["kind"] == "caption"), 0)
        self.assertEqual({seat["group"] for seat in seats.values() if seat["group"]}, {"K Table 1", "K Table 2"})
        table = seats["K Table 1 · 4"]
        self.assertEqual((table["row"], table["column"], table["column_span"], table["group_size"], table["seat_index"], table["display"]), (1, 2, 6, 4, 4, "4"))
        self.assertEqual((seats["K1"]["row"], seats["K1"]["column"], seats["K1"]["row_label"], seats["K1"]["number"]), (1, 8, "K", 1))
        self.assertEqual((seats["A16"]["row"], seats["A16"]["column"]), (10, 17))
        self.assertNotIn("K K", seats)
        self.assertEqual(sum(seat["broken"] for seat in seats.values()), 0)
        self.assertEqual(chart["warnings"], [])

    def test_department_chart_hecc_207_broken_seats_and_single_chair(self):
        chart = parse_seating_chart(department_chart("HECC 207"), "HECC 207.xlsx")
        seats = by_label(chart)
        self.assertEqual(len(seats), 258)
        self.assertEqual({label for label, seat in seats.items() if seat["broken"]}, {"K7", "K18", "K19", "J7", "H18", "G18", "E22", "C20"})
        self.assertEqual(seats["K7"]["display"], "7")
        self.assertEqual((seats["L Chair"]["row"], seats["L Chair"]["column"], seats["L Chair"]["display"]), (1, 22, "Chair"))
        self.assertEqual({seat["group"]: seat["group_size"] for seat in seats.values() if seat["group"]}, {"L Table 1": 3, "L Table 2": 2})
        self.assertEqual(chart["warnings"], [])

    def test_department_chart_hecc_209_merged_captions_and_annotation_rows(self):
        chart = parse_seating_chart(department_chart("HECC 209"), "HECC 209.xlsx")
        seats = by_label(chart)
        self.assertEqual(len(seats), 142)
        self.assertEqual((chart["rows"], chart["columns"]), (13, 19))
        self.assertEqual({label for label, seat in seats.items() if seat["broken"]}, {"G2", "G8", "F4", "E3", "E12", "D4", "D15"})
        captions = {(item["text"], item["row"], item["column"], item["row_span"], item["column_span"]) for item in chart["landmarks"] if item["kind"] == "caption"}
        self.assertEqual(captions, {("Door", 1, 2, 2, 1), ("HECC 209 (BACK OF THE ROOM)", 1, 3, 2, 16), ("Door", 1, 19, 2, 1),
                                    ("STAIRS", 3, 2, 10, 1), ("STAIRS", 3, 19, 10, 1), ("FRONT OF THE ROOM", 13, 1, 1, 19)})
        self.assertEqual([item["text"] for item in chart["landmarks"] if item["kind"] == "row"], list("KJHGFEDCBA"))
        # "TABLE" beside "3 Chairs" is one table spanning both cells.
        table = seats["K Table 1 · 1"]
        self.assertEqual((table["row"], table["column"], table["column_span"], table["group_size"]), (3, 5, 2, 3))
        self.assertEqual((seats["K5"]["row"], seats["K5"]["column"]), (3, 7))
        self.assertEqual((seats["J1"]["row"], seats["A15"]["row"]), (4, 12))
        self.assertEqual(chart["warnings"], [])

    def test_row_letter_layout_rules(self):
        cells = {"A1": "K", "B1": "Table", "C1": "3 chairs", "D1": "1", "E1": "2 broken", "F1": "Chair", "G1": "Chair", "H1": "K",
                 "A2": "J", "B2": "Broken", "C2": "LH", "D2": "Bench (2 seats)", "F2": "Desk",
                 "A3": "J", "B3": "1", "C3": "2", "D3": "3", "F3": "4 windowside", "H3": "J",
                 "A5": "Front of room"}
        chart = parse_seating_chart(chart_bytes(cells, merges=("A2:A3",)), "room.xlsx")
        seats = by_label(chart)
        self.assertEqual(sorted(seats), sorted(["K Table 1 · 1", "K Table 1 · 2", "K Table 1 · 3", "K1", "K2", "K Chair", "K Chair 2",
                                                "J Table 1 · 1", "J Table 1 · 2", "J1", "J2", "J3", "J4"]))
        self.assertTrue(seats["K2"]["broken"])
        self.assertTrue(seats["J1"]["broken"])
        self.assertTrue(seats["J2"]["left_handed"])
        self.assertEqual((seats["K Table 1 · 1"]["column"], seats["K Table 1 · 1"]["column_span"]), (2, 2))
        self.assertEqual((seats["J Table 1 · 1"]["row"], seats["J Table 1 · 1"]["column"]), (2, 4))
        # Row 4 is blank and outside every lettered band, so it stays as an aisle.
        self.assertEqual((seats["J1"]["row"], chart["rows"]), (3, 5))
        captions = [(item["text"], item["row"], item["column"]) for item in chart["landmarks"] if item["kind"] == "caption"]
        self.assertEqual(captions, [("Desk", 2, 6), ("Front of room", 5, 1)])
        self.assertEqual(len(chart["warnings"]), 2)
        self.assertIn("Cell F2 says “Desk” without a seat count", chart["warnings"][0])
        self.assertIn("note “windowside” was not understood", chart["warnings"][1])

    def test_annotation_without_a_seat_is_reported(self):
        chart = parse_seating_chart(chart_bytes({"A1": "K", "B1": "1", "A2": "J", "C2": "Broken", "B3": "9"}), "room.xlsx")
        self.assertEqual(labels(chart), ["K1", "9"])
        self.assertFalse(any(seat["broken"] for seat in chart["seats"]))
        self.assertEqual(len(chart["warnings"]), 1)
        self.assertIn("Cell C2 says “broken” but no seat", chart["warnings"][0])

    def test_tapered_rows_and_repeated_aisle_markers_are_not_seats_or_annotations(self):
        content = chart_bytes({"A1": "D", "B1": 1, "C1": 2, "D1": "D",
                               "B2": "C", "C2": 1, "D2": 2, "E2": "C",
                               "C3": "B", "D3": 1, "E3": "B", "F3": 2, "G3": "B",
                               "D4": "A", "E4": "1 Broken", "F4": 2, "G4": "A"})
        chart = parse_seating_chart(content, "tapered.xlsx")
        self.assertEqual(labels(chart), ["D1", "D2", "C1", "C2", "B1", "B2", "A1", "A2"])
        self.assertEqual([s["label"] for s in chart["seats"] if s["broken"]], ["A1"])
        self.assertEqual(chart["warnings"], [])

    def test_independently_lettered_banks_keep_their_own_seat_numbers(self):
        chart = parse_seating_chart(chart_bytes({"A1": "D", "B1": 1, "C1": 2, "E1": 1, "F1": 2, "G1": "H",
                                                "A2": "C", "B2": 1, "C2": 2, "E2": 1, "F2": 2, "G2": "G"}), "banks.xlsx")
        self.assertEqual(labels(chart), ["D1", "D2", "H1", "H2", "C1", "C2", "G1", "G2"])
        self.assertEqual(chart["review_required"], [])

    def test_named_tables_scope_seats_above_and_below_their_caption(self):
        content = chart_bytes({"B1": 1, "C1": 2, "E1": 1, "F1": 2,
                               "B2": "Table A", "E2": "Table B",
                               "B3": 3, "C3": 4, "E3": 3, "F3": 4}, merges=("B2:C2", "E2:F2"))
        chart = parse_seating_chart(content, "tables.xlsx")
        seats = by_label(chart)
        self.assertEqual(set(seats), {f"Table {table} · {number}" for table in "AB" for number in range(1, 5)})
        self.assertEqual((seats["Table A · 3"]["group"], seats["Table A · 3"]["group_size"]), ("Table A", 4))
        self.assertEqual(chart["warnings"], [])

    def test_source_notes_and_loose_left_desk_keep_their_meaning(self):
        chart = parse_seating_chart(chart_bytes({"A1": "No left-hand seats", "B1": "B", "C1": 1, "D1": "B",
                                                "A2": "A", "B2": "1 loose left desk", "C2": 1, "D2": "A"}), "desk.xlsx")
        self.assertEqual(labels(chart), ["B1", "A Loose left desk 1", "A1"])
        self.assertTrue(by_label(chart)["A Loose left desk 1"]["left_handed"])
        self.assertEqual(chart["warnings"], ["Source note at A1: No left-hand seats"])

    def test_uncounted_movable_tables_have_no_assignable_seats(self):
        for note in ("Movable Tables (no #'s)", "Moveable Tables (no #'s)"):
            with self.subTest(note=note):
                chart = parse_seating_chart(chart_bytes({"A1": note}), "movable.xlsx")
                self.assertEqual(chart["seats"], [])
                self.assertEqual(chart["landmarks"][0]["text"], note)
                self.assertIn("no assignable seats", chart["warnings"][-1])
        with self.assertRaisesRegex(ValueError, "no seats"):
            parse_seating_chart(chart_bytes({"A1": "Front"}), "captions.xlsx")
        chart = parse_seating_chart(chart_bytes({"A1": "A1", "A2": "All Tables (3 extra chairs in back)"}), "extra.xlsx")
        self.assertEqual(labels(chart), ["A1"])
        self.assertEqual(chart["review_required"], ["Source also lists 3 extra chairs in back without numbered seat locations."])

    def test_review_import_qualifies_every_duplicate_and_preserves_unknown_notes(self):
        content = chart_bytes({"A1": "G", "B1": 6, "C1": "6H (moveable chair)", "D1": "1 Wheelchair Only", "E1": "2 wobbly",
                               "A2": "F", "B2": 6})
        with self.assertRaisesRegex(ValueError, "both describe seat “G6”"):
            parse_seating_chart(content, "ambiguous.xlsx")
        chart = parse_seating_chart(content, "ambiguous.xlsx", allow_ambiguous=True)
        self.assertEqual(labels(chart), ["G6 · B1", "G6 · C1", "G1", "G2", "F6"])
        self.assertEqual([s["display"] for s in chart["seats"]], ["6", "6", "1", "2", "6"])
        self.assertEqual(len(chart["review_required"]), 2)
        self.assertTrue(any("wobbly" in item for item in chart["review_required"]))
        self.assertFalse(any(s["broken"] or s["left_handed"] for s in chart["seats"]))

    def test_accessible_seats_and_wheelchair_spaces_are_kept_free(self):
        chart = parse_seating_chart(chart_bytes({"A1": "B", "B1": 1, "C1": "2 H", "D1": "3H (moveable chair)", "E1": "4 Wheelchair Only",
                                                "A2": "A", "B2": 1, "C2": 2, "C3": "Accessible"}), "accessible.xlsx")
        seats = by_label(chart)
        self.assertEqual([label for label, seat in seats.items() if seat.get("blocked")], ["B2", "B3", "B4", "A2"])
        self.assertIn("Accessible seat", seats["B2"]["notes"])
        self.assertIn("Wheelchair space", seats["B4"]["notes"])
        self.assertIn("“H (moveable chair)”", seats["B3"]["notes"])
        self.assertEqual((chart["review_required"], chart["warnings"]), ([], []))
        stray = parse_seating_chart(chart_bytes({"A1": "A", "B1": 1, "D2": "Accessible"}), "stray.xlsx")
        self.assertEqual(stray["warnings"], ["Cell D2 says “accessible” but no seat is in that column of its row."])

    def test_blank_rows_around_the_chart_are_ignored(self):
        # Department files carry formatted but empty rows and columns far past the chart,
        # and often start well below row 1. Only the filled span counts toward the limit.
        workbook = Workbook()
        sheet = workbook.active
        sheet["C500"] = "Front"
        sheet["C502"], sheet["D502"], sheet["C503"], sheet["E503"] = "A1", "A2", "B1", "B2"
        for reference in ("A1", "B250", "C2000", "AZ5000", "C503"):
            sheet[reference].font = Font(bold=True)
        sheet["D700"] = "   "
        buffer = io.BytesIO()
        workbook.save(buffer)
        chart = parse_seating_chart(buffer.getvalue(), "hall.xlsx")
        self.assertEqual((chart["rows"], chart["columns"]), (4, 3))
        self.assertEqual([(item["row"], item["column"], item["text"]) for item in chart["landmarks"]], [(1, 1, "Front")])
        self.assertEqual([(seat["row"], seat["column"], seat["label"]) for seat in chart["seats"]],
                         [(3, 1, "A1"), (3, 2, "A2"), (4, 1, "B1"), (4, 3, "B2")])
        sheet["C720"] = "stray note"
        buffer = io.BytesIO()
        workbook.save(buffer)
        with self.assertRaisesRegex(ValueError, "spans 221 rows from C500 to E720"):
            parse_seating_chart(buffer.getvalue(), "hall.xlsx")

    def test_invalid_charts_are_rejected_with_cell_references(self):
        cases = (
            (b"", "room.xlsx", "nonempty"),
            (chart_bytes(), "room.csv", ".xlsx"),
            (b"not a workbook", "room.xlsx", "could not be read"),
            (chart_bytes({"A1": "K", "B1": "7", "C1": "7", "A2": "J"}), "room.xlsx", "B1 and C1 both describe seat “K7”"),
            (chart_bytes({"A1": "A1", "B1": "=A1"}), "room.xlsx", "Cell B1 contains a formula"),
            (chart_bytes({"A1": "Front", "B2": "Door"}), "room.xlsx", "no seats, only captions"),
            (chart_bytes({"A1": "x" * 41}), "room.xlsx", "longer than 40"),
            (chart_bytes({"A1": "Table (90 seats)"}), "room.xlsx", "between 1 and 50"),
            (chart_bytes({"A1": "A1", "A201": "A2"}), "room.xlsx", "spans 201 rows from A1 to A201"),
            (chart_bytes({"A1": "A1", "CX1": "A2"}), "room.xlsx", "spans 102 columns from A1 to CX1"),
        )
        for content, filename, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                parse_seating_chart(content, filename)
        with self.assertRaisesRegex(ValueError, "no seats"):
            parse_seating_chart(chart_bytes({}), "room.xlsx")
        # Repeated text labels are unnumbered furniture, so they get a running number.
        self.assertEqual(labels(parse_seating_chart(chart_bytes({"A1": "A1", "C2": "a1", "D2": "A1"}), "room.xlsx")), ["A1", "a1 2", "A1 3"])


class RoomApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()

    def create(self, name="BLOC 117", cells=CHART, notes="", filename="chart.xlsx"):
        return self.client.post("/api/rooms", data={"name": name, "notes": notes, "file": (io.BytesIO(chart_bytes(cells)), filename)}, headers=HEADERS)

    def seats(self, room):
        return {seat["label"]: seat for seat in room["seats"]}

    def test_no_session_needed_and_same_origin_guards(self):
        # No browser session cookie is involved; a fresh client can read and upload.
        self.assertEqual(self.app.test_client().get("/api/rooms").json, [])
        self.assertEqual(self.app.test_client().post("/api/rooms", data={"name": "Fresh", "file": (io.BytesIO(chart_bytes()), "c.xlsx")}, headers=HEADERS).status_code, 201)
        self.assertEqual(self.client.post("/api/rooms", data={"name": "x", "file": (io.BytesIO(chart_bytes()), "c.xlsx")}).status_code, 403)
        self.assertEqual(self.client.post("/api/rooms", data={"name": "x", "file": (io.BytesIO(chart_bytes()), "c.xlsx")}, headers={**HEADERS, "Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.post("/api/rooms", json={"name": "x"}, headers=HEADERS).status_code, 415)
        self.assertEqual(self.client.post("/api/rooms", data={"name": "x"}, headers=HEADERS).status_code, 415)
        self.assertEqual(self.client.post("/api/rooms", data={"name": "x", "file": (io.BytesIO(b""), "")}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/rooms", data={"name": "", "file": (io.BytesIO(chart_bytes()), "c.xlsx")}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/rooms", data={"name": "x", "file": (io.BytesIO(chart_bytes()), "c.csv")}, headers=HEADERS).status_code, 400)
        self.assertEqual([room["name"] for room in self.client.get("/api/state").json["rooms"]], ["Fresh"])

    def test_create_list_detail_state_and_export(self):
        response = self.create(notes="  Tablet arms ")
        self.assertEqual(response.status_code, 201, response.json)
        room = response.json
        self.assertEqual((room["name"], room["notes"], room["sheet"]), ("BLOC 117", "Tablet arms", "Room"))
        self.assertEqual((room["grid_rows"], room["grid_columns"], room["seat_count"]), (4, 4, 6))
        self.assertEqual((room["landmarks"][0]["row"], room["landmarks"][0]["column"], room["landmarks"][0]["text"]), (1, 2, "Front of room"))
        self.assertEqual((room["seats"][0]["display"], room["seats"][0]["row_span"], room["seats"][0]["group"]), ("A1", 1, ""))
        self.assertEqual(room["source_filename"], "chart.xlsx")
        self.assertEqual(len(room["source_sha256"]), 64)
        self.assertEqual(len(room["imports"]), 1)
        self.assertEqual(room["imports"][0]["added"], 6)
        self.assertEqual(self.create(name="bloc 117").status_code, 409)
        listing = self.client.get("/api/rooms").json
        self.assertEqual([item["name"] for item in listing], ["BLOC 117"])
        self.assertNotIn("seats", listing[0])
        # Runs A1-A2, A3, B1-B2, B3 allow one spaced seat each pair: 4 of 6 seats with a gap between neighbours.
        self.assertEqual((listing[0]["spaced_count"], room["spaced_count"]), (4, 4))
        state = self.client.get("/api/state").json["rooms"]
        self.assertEqual((state[0]["seat_count"], state[0]["broken_count"], state[0]["left_handed_count"], state[0]["blocked_count"], state[0]["usable_count"]), (6, 0, 0, 0, 6))
        detail = self.client.get(f"/api/rooms/{room['id']}").json
        self.assertEqual(detail["seats"], room["seats"])
        export = self.client.get("/api/export").json
        self.assertEqual(export["schema_version"], 4)
        self.assertEqual(len(export["rooms"][0]["seats"]), 6)
        self.assertEqual(self.client.get("/api/rooms/missing").status_code, 404)

    def test_seat_conditions_versions_and_validation(self):
        room = self.create().json
        route = f"/api/rooms/{room['id']}/seats"
        seats = self.seats(room)
        ids = [seats["A1"]["id"], seats["A2"]["id"]]
        updated = self.client.post(route, json={"version": room["version"], "seat_ids": ids, "broken": True}, headers=HEADERS).json
        self.assertEqual(updated["broken_count"], 2)
        # The list's spaced capacity (remembered per exact usable-seat geometry) follows every condition change, as the room's does.
        listed = lambda: next(item for item in self.client.get("/api/rooms").json if item["id"] == room["id"])["spaced_count"]
        self.assertEqual((listed(), updated["spaced_count"]), (3, 3))
        self.assertEqual(updated["version"], room["version"] + 1)
        self.assertTrue(self.seats(updated)["A1"]["broken"])
        self.assertFalse(self.seats(updated)["A1"]["left_handed"])
        stale = self.client.post(route, json={"version": room["version"], "seat_ids": ids, "left_handed": True}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        both = self.client.post(route, json={"version": updated["version"], "seat_ids": [ids[0]], "left_handed": True}, headers=HEADERS).json
        self.assertEqual((both["broken_count"], both["left_handed_count"]), (2, 1))
        self.assertTrue(self.seats(both)["A1"]["broken"] and self.seats(both)["A1"]["left_handed"])
        blocked = self.client.post(route, json={"version": both["version"], "seat_ids": [ids[0], seats["A3"]["id"]], "blocked": True}, headers=HEADERS).json
        # A3 is blocked only; A1 is broken, left-handed, and blocked at once but counts once against usable seats.
        self.assertEqual((blocked["broken_count"], blocked["blocked_count"], blocked["usable_count"]), (2, 2, 3))
        self.assertTrue(self.seats(blocked)["A3"]["blocked"] and not self.seats(blocked)["A3"]["broken"])
        self.assertEqual((listed(), blocked["spaced_count"]), (2, 2))
        cleared = self.client.post(route, json={"version": blocked["version"], "seat_ids": ids + [seats["A3"]["id"]], "broken": False, "left_handed": False, "blocked": False}, headers=HEADERS).json
        self.assertEqual((cleared["broken_count"], cleared["left_handed_count"], cleared["blocked_count"], cleared["usable_count"]), (0, 0, 0, 6))
        self.assertEqual((listed(), cleared["spaced_count"]), (4, 4))
        state = self.client.get("/api/state").json["rooms"][0]
        self.assertEqual((state["broken_count"], state["left_handed_count"], state["version"]), (0, 0, cleared["version"]))
        for payload, status in (
            ({"version": cleared["version"], "seat_ids": [], "broken": True}, 400),
            ({"version": cleared["version"], "seat_ids": [{}], "broken": True}, 400),
            ({"version": cleared["version"], "seat_ids": [[]], "broken": True}, 400),
            ({"version": cleared["version"], "seat_ids": ids}, 400),
            ({"version": cleared["version"], "seat_ids": ids, "wobbly": True}, 400),
            ({"version": cleared["version"], "seat_ids": ids, "broken": "yes"}, 400),
            ({"version": cleared["version"], "seat_ids": ids, "blocked": 1}, 400),
            ({"version": cleared["version"], "seat_ids": ids + ["not-a-seat"], "broken": True}, 409),
            ({"version": "1", "seat_ids": ids, "broken": True}, 409),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(route, json=payload, headers=HEADERS).status_code, status)
        self.assertEqual(self.client.get(f"/api/rooms/{room['id']}").json["version"], cleared["version"])

    def test_rename_replace_chart_and_delete(self):
        room = self.create().json
        other = self.create(name="HELD 100", cells={"A1": "1", "A2": "2"}).json
        route = f"/api/rooms/{room['id']}"
        renamed = self.client.patch(route, json={"version": room["version"], "name": " BLOC  118 ", "notes": "Renamed"}, headers=HEADERS).json
        self.assertEqual((renamed["name"], renamed["notes"]), ("BLOC 118", "Renamed"))
        self.assertEqual(self.client.patch(route, json={"version": room["version"], "name": "x"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.patch(route, json={"version": renamed["version"], "name": "held 100"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.patch(route, json={"version": renamed["version"], "name": ""}, headers=HEADERS).status_code, 400)

        seats = self.seats(renamed)
        flagged = self.client.post(route + "/seats", json={"version": renamed["version"], "seat_ids": [seats["A1"]["id"], seats["B3"]["id"]], "broken": True}, headers=HEADERS).json
        flagged = self.client.post(route + "/seats", json={"version": flagged["version"], "seat_ids": [seats["A2"]["id"]], "left_handed": True}, headers=HEADERS).json
        # A corrected chart moves A1, changes the case of a2, drops B3, and adds C1.
        replacement = {"A1": "Door", "B2": "a1", "C2": "a2", "B3": "B1", "C3": "B2", "D3": "C1"}
        stale = self.client.post(route + "/chart", data={"version": str(renamed["version"]), "file": (io.BytesIO(chart_bytes(replacement)), "v2.xlsx")}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.post(route + "/chart", json={"version": flagged["version"]}, headers=HEADERS).status_code, 415)
        replaced = self.client.post(route + "/chart", data={"version": str(flagged["version"]), "file": (io.BytesIO(chart_bytes(replacement)), "v2.xlsx")}, headers=HEADERS)
        self.assertEqual(replaced.status_code, 200, replaced.json)
        replaced = replaced.json
        new_seats = self.seats(replaced)
        self.assertEqual(sorted(new_seats), ["B1", "B2", "C1", "a1", "a2"])
        self.assertEqual(new_seats["a1"]["id"], seats["A1"]["id"])
        self.assertTrue(new_seats["a1"]["broken"])
        self.assertTrue(new_seats["a2"]["left_handed"])
        self.assertEqual((new_seats["a1"]["row"], new_seats["a1"]["column"]), (2, 2))
        self.assertEqual((replaced["broken_count"], replaced["left_handed_count"], replaced["seat_count"]), (1, 1, 5))
        self.assertEqual([(item["row"], item["column"], item["text"]) for item in replaced["landmarks"]], [(1, 1, "Door")])
        self.assertEqual((replaced["imports"][0]["added"], replaced["imports"][0]["kept"], replaced["imports"][0]["removed"]), (1, 4, 2))
        self.assertEqual(replaced["source_filename"], "v2.xlsx")
        self.assertEqual(self.client.post(route + "/seats", json={"version": replaced["version"], "seat_ids": [seats["B3"]["id"]], "broken": True}, headers=HEADERS).status_code, 409)

        self.assertEqual(self.client.delete(route, json={"version": renamed["version"]}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.delete(route, json={"version": replaced["version"]}, headers=HEADERS).status_code, 204)
        self.assertEqual(self.client.get(route).status_code, 404)
        self.assertEqual([item["id"] for item in self.client.get("/api/rooms").json], [other["id"]])

    def test_department_chart_marks_broken_seats_and_reimport_adds_only(self):
        response = self.client.post("/api/rooms", data={"name": "HECC 207", "file": (io.BytesIO(department_chart("HECC 207")), "HECC 207.xlsx")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        room = response.json
        self.assertEqual((room["seat_count"], room["broken_count"], room["warnings"]), (258, 8, []))
        hall = self.client.post("/api/rooms", data={"name": "HECC 105", "file": (io.BytesIO(department_chart("HECC 105")), "HECC 105.xlsx")}, headers=HEADERS).json
        self.assertEqual(hall["spaced_count"], 75)  # 9 rows of 16 -> 72, plus two tables and the K1-K2 pair.
        self.assertEqual(room["imports"][0]["chart_broken"], 8)
        seats = self.seats(room)
        self.assertEqual((seats["K7"]["broken"], seats["K7"]["display"], seats["L Table 1 · 2"]["group_size"]), (True, "7", 3))
        cleared = self.client.post(f"/api/rooms/{room['id']}/seats", json={"version": room["version"], "seat_ids": [seats["K7"]["id"], seats["K8"]["id"]], "broken": False, "left_handed": True}, headers=HEADERS).json
        self.assertEqual((cleared["broken_count"], cleared["left_handed_count"]), (7, 2))
        cleared = self.client.post(f"/api/rooms/{room['id']}/seats", json={"version": cleared["version"], "seat_ids": [seats["A1"]["id"]], "blocked": True}, headers=HEADERS).json
        self.assertEqual((cleared["blocked_count"], cleared["usable_count"]), (1, 258 - 7 - 1))
        again = self.client.post(f"/api/rooms/{room['id']}/chart", data={"version": str(cleared["version"]), "file": (io.BytesIO(department_chart("HECC 207")), "HECC 207.xlsx")}, headers=HEADERS).json
        # The chart's own marks add conditions again but never remove the ones you set.
        self.assertEqual((again["broken_count"], again["left_handed_count"], again["blocked_count"], again["seat_count"]), (8, 2, 1, 258))
        self.assertTrue(self.seats(again)["A1"]["blocked"])
        self.assertEqual(self.seats(again)["K7"]["id"], seats["K7"]["id"])
        self.assertEqual((again["imports"][0]["kept"], again["imports"][0]["added"]), (258, 0))
        with_notes = self.client.post("/api/rooms", data={"name": "Notes", "file": (io.BytesIO(chart_bytes({"A1": "K", "B1": "Desk", "C1": "4 mystery", "A2": "J", "B2": "1"})), "n.xlsx")}, headers=HEADERS).json
        self.assertEqual(len(with_notes["warnings"]), 2)
        self.assertEqual(with_notes["imports"][0]["warnings"], with_notes["warnings"])

    def test_a_charts_accessible_marks_keep_seats_free_and_a_reupload_never_clears_yours(self):
        cells = {"A1": "B", "B1": 1, "C1": "2 H", "D1": 3, "A2": "A", "B2": "1 Wheelchair Only", "C2": 2}
        room = self.create(name="HELD 107", cells=cells).json
        seats = self.seats(room)
        self.assertEqual((room["blocked_count"], room["usable_count"], room["review_required"], room["warnings"]), (2, 3, [], []))
        self.assertEqual(seats["B2"]["notes"], "Accessible seat (marked “H” on the department chart); kept free for a student who needs it.")
        self.assertIn("Wheelchair space (marked “Wheelchair Only”", seats["A1"]["notes"])
        mine = self.client.post(f"/api/rooms/{room['id']}/seats", json={"version": room["version"], "seat_ids": [seats["B3"]["id"]], "blocked": True}, headers=HEADERS).json
        again = self.client.post(f"/api/rooms/{room['id']}/chart", data={"version": str(mine["version"]), "file": (io.BytesIO(chart_bytes(cells)), "chart.xlsx")}, headers=HEADERS).json
        self.assertEqual([label for label, seat in self.seats(again).items() if seat["blocked"]], ["B2", "B3", "A1"])
        self.assertEqual(self.seats(again)["B2"]["id"], seats["B2"]["id"])

    def test_first_seat_schema_is_rebuilt_without_losing_rows(self):
        path = Path(self.directory.name) / "legacy.sqlite3"
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE rooms (id TEXT PRIMARY KEY, name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', sheet TEXT NOT NULL DEFAULT '',
                grid_rows INTEGER NOT NULL DEFAULT 0, grid_columns INTEGER NOT NULL DEFAULT 0, landmarks TEXT NOT NULL DEFAULT '[]',
                source_filename TEXT NOT NULL DEFAULT '', source_sha256 TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE room_seats (id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), label TEXT NOT NULL,
                grid_row INTEGER NOT NULL, grid_column INTEGER NOT NULL, broken INTEGER NOT NULL DEFAULT 0,
                left_handed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(room_id, label COLLATE NOCASE), UNIQUE(room_id, grid_row, grid_column));
            CREATE TABLE room_imports (id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), filename TEXT NOT NULL,
                sha256 TEXT NOT NULL, sheet TEXT NOT NULL, seat_count INTEGER NOT NULL, added INTEGER NOT NULL,
                removed INTEGER NOT NULL, kept INTEGER NOT NULL, imported_at TEXT NOT NULL);
            INSERT INTO rooms (id, name, created_at, updated_at) VALUES ('r1', 'Old', 't', 't');
            INSERT INTO room_seats VALUES ('s1', 'r1', 'A1', 2, 3, 1, 0, 't', 't');
        """)
        db.commit()
        db.close()
        initialize_rooms(path)
        initialize_rooms(path)
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        seat = db.execute("SELECT * FROM room_seats").fetchone()
        self.assertEqual((seat["label"], seat["display"], seat["grid_row"], seat["grid_column"], seat["broken"], seat["row_span"], seat["seat_index"]), ("A1", "A1", 2, 3, 1, 1, None))
        self.assertIn("warnings", {row["name"] for row in db.execute("PRAGMA table_info(room_imports)")})
        self.assertIn("blocked", {row["name"] for row in db.execute("PRAGMA table_info(room_seats)")})
        db.close()

    def test_rooms_survive_restart(self):
        room = self.create().json
        seats = self.seats(room)
        self.client.post(f"/api/rooms/{room['id']}/seats", json={"version": room["version"], "seat_ids": [seats["B2"]["id"]], "broken": True}, headers=HEADERS)
        reopened = create_app(self.directory.name)
        reopened.testing = True
        client = reopened.test_client()
        detail = client.get(f"/api/rooms/{room['id']}").json
        self.assertEqual(detail["name"], "BLOC 117")
        self.assertTrue(self.seats(detail)["B2"]["broken"])
        self.assertEqual(client.get("/api/state").json["rooms"][0]["broken_count"], 1)


if __name__ == "__main__":
    unittest.main()
