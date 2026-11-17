"""Recipient PDFs carry a highlighted seat and occupancy, with no student names."""

import copy
import io
import json
from pathlib import Path
import unittest

from pypdf import PdfReader

from room_import import parse_seating_chart
from room_pdf_import import _profile, source_index
from seating_student_pdf import render_pdf, sanitize_chart


def chart_from_room(parsed):
    room = copy.deepcopy(parsed)
    room.update(name="Synthetic Hall", grid_rows=room.pop("rows", 1), grid_columns=room.pop("columns", 1))
    roster = []
    for index, seat in enumerate(room["seats"]):
        seat.update(id=f"private-seat-id-{index}", notes="PRIVATE-SEAT-NOTE", exam_version="PRIVATE-VERSION")
        if index < 2:
            student = {"enrollment_id": f"private-enrollment-{index}",
                       "name": "Jordan Sample" if index == 0 else "OTHER-STUDENT-SECRET",
                       "seat_id": seat["id"], "seat": seat["label"], "status": "seated",
                       "uin": "PRIVATE-UIN", "email": "private-email@example.test",
                       "card": {"detail": "PRIVATE-CARD-DETAIL", "token": "PRIVATE-TOKEN"}}
            roster.append(student)
            seat["student"] = copy.deepcopy(student)
        else:
            seat["student"] = None
    return {"id": "private-chart-id", "title": "Practice Exam", "code": "TEST 101", "term_label": "Fall 2026",
            "room_name": room["name"], "room": room, "students": roster,
            "notes": "PRIVATE-CHART-NOTE", "issues": ["OTHER-STUDENT-SECRET"],
            "exam_versions": {"rows": [{"name": "OTHER-STUDENT-SECRET"}]}}


def simple_chart():
    return chart_from_room({"rows": 3, "columns": 7, "landmarks": [
        {"kind": "caption", "text": "Front of room", "row": 1, "column": 2, "column_span": 6}],
        "seats": [{"label": label, "row": row, "column": column} for label, row, column in
                  (("A1", 2, 2), ("A2", 2, 4), ("A3", 2, 7), ("B1", 3, 2), ("B2", 3, 4))]})


class StudentPdfTests(unittest.TestCase):
    def assert_private(self, snapshot, pdf):
        reader = PdfReader(io.BytesIO(pdf))
        representations = [json.dumps(snapshot), str(reader.metadata)]
        representations += [page.extract_text() for page in reader.pages]
        representations += [page.get_contents().get_data().decode("latin-1") for page in reader.pages]
        for secret in ("Jordan Sample", "OTHER-STUDENT-SECRET", "private-enrollment", "private-seat-id", "private-chart-id",
                       "PRIVATE-UIN", "private-email", "PRIVATE-CARD-DETAIL", "PRIVATE-TOKEN",
                       "PRIVATE-SEAT-NOTE", "PRIVATE-CHART-NOTE", "PRIVATE-VERSION"):
            for representation in representations:
                self.assertNotIn(secret, representation)
        self.assertTrue(any("Your seat" in page.extract_text() for page in reader.pages))
        return reader

    def test_allowlisted_snapshot_and_pdf_redact_every_other_student(self):
        source = simple_chart()
        original = copy.deepcopy(source)
        snapshot = sanitize_chart(source, "private-enrollment-0")
        self.assertEqual(source, original)
        self.assertEqual(snapshot["recipient"], {"seat": "A1"})
        self.assertEqual([seat["state"] for seat in snapshot["room"]["seats"]], ["own", "occupied", "empty", "empty", "empty"])
        pdf = render_pdf(snapshot)
        reader = self.assert_private(snapshot, pdf)
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("Occupied", text)
        self.assertIn("Front of room", text)
        self.assertIn("TEST 101", text)
        self.assertIn("Practice Exam", text)
        self.assertEqual(pdf, render_pdf(json.loads(json.dumps(snapshot))))
        # Sparse workbook positions preserve the two different aisle widths.
        a, b, c = snapshot["room"]["seats"][:3]
        self.assertGreater(c["x"] - b["x"], b["x"] - a["x"])

    def test_other_occupied_seats_are_gray_even_with_private_card_fields(self):
        source = simple_chart()
        source["room"]["seats"][1]["student"]["card"]["state"] = "complete"
        source["room"]["seats"][2]["broken"] = True
        source["room"]["seats"][3]["blocked"] = True
        source["room"]["seats"][4]["left_handed"] = True
        snapshot = sanitize_chart(source, "private-enrollment-0")
        self.assertEqual([seat["state"] for seat in snapshot["room"]["seats"]],
                         ["own", "occupied", "unavailable", "unavailable", "empty"])
        reader = self.assert_private(snapshot, render_pdf(snapshot))
        self.assertIn("Unavailable", reader.pages[0].extract_text())
        self.assertNotIn("complete", json.dumps(snapshot))
        self.assertNotIn("left_handed", json.dumps(snapshot))
        operations = reader.pages[0].get_contents().get_data()
        self.assertIn(b".839216 .909804 1 rg", operations)  # own blue
        self.assertIn(b".862745 .878431 .898039 rg", operations)  # occupied gray

    def test_no_ambiguous_missing_unusable_or_dr_recipient(self):
        for mutation in ("missing", "duplicate", "broken", "blocked", "dr", "mismatch"):
            with self.subTest(mutation=mutation):
                source = simple_chart()
                if mutation == "missing":
                    source["students"] = []
                elif mutation == "duplicate":
                    source["students"].append(copy.deepcopy(source["students"][0]))
                elif mutation in ("broken", "blocked"):
                    source["room"]["seats"][0][mutation] = True
                elif mutation == "dr":
                    source["students"][0]["status"] = "dr_testing"
                elif mutation == "mismatch":
                    source["students"][0]["seat_id"] = "wrong-seat"
                with self.assertRaises(ValueError):
                    sanitize_chart(source, "private-enrollment-0")

    def test_real_workbook_tables_keep_every_chair_and_group_boundary(self):
        fixture = Path(__file__).parent / "fixtures" / "rooms" / "HECC 105.xlsx"
        if not fixture.exists():  # the public copy leaves out the department's seating charts
            self.skipTest(f"the department's seating chart {fixture.name} is not in this copy")
        source = chart_from_room(parse_seating_chart(fixture.read_bytes(), fixture.name))
        snapshot = sanitize_chart(source, "private-enrollment-0")
        self.assertEqual(len(snapshot["room"]["seats"]), 154)
        table = [seat for seat in snapshot["room"]["seats"] if seat["label"].startswith("K Table 1")]
        self.assertEqual(len(table), 4)
        for first, second in zip(table, table[1:]):
            self.assertLessEqual(first["x"] + first["width"], second["x"])
        features = snapshot["room"]["features"]
        self.assertTrue(any(item.get("text") == "K Table 1" for item in features))
        reader = self.assert_private(snapshot, render_pdf(snapshot))
        self.assertEqual(len(reader.pages), 2)
        overview = reader.pages[0].extract_text()
        for seat in snapshot["room"]["seats"]:
            self.assertIn(seat["label"], " ".join(overview.split()))

    def test_narrow_tables_expand_their_row_without_overlapping_the_next(self):
        source = simple_chart()
        source["room"]["grid_rows"] = 4
        for index in range(6):
            source["room"]["seats"].append({"id": f"table-{index}", "label": f"Table · {index + 1}",
                "row": 3, "column": 7, "group": "Table", "seat_index": index + 1, "student": None})
        source["room"]["seats"].append({"id": "after-table", "label": "C7", "row": 4, "column": 7, "student": None})
        snapshot = sanitize_chart(source, "private-enrollment-0")
        table = [seat for seat in snapshot["room"]["seats"] if seat["label"].startswith("Table")]
        after = next(seat for seat in snapshot["room"]["seats"] if seat["label"] == "C7")
        for first, second in zip(table, table[1:]):
            self.assertLessEqual(first["y"] + first["height"], second["y"])
        self.assertLessEqual(table[-1]["y"] + table[-1]["height"], after["y"])

    def test_all_reviewed_floorplans_keep_physical_geometry_and_architecture(self):
        if not source_index():
            self.skipTest("the public copy leaves out the reviewed ILCB plans")
        for digest, entry in source_index().items():
            with self.subTest(room=entry["room"]):
                source = chart_from_room(_profile(digest))
                # Unknown fields never enter the delivered snapshot.
                source["room"]["layout"]["features"][0]["private"] = "OTHER-STUDENT-SECRET"
                snapshot = sanitize_chart(source, "private-enrollment-0")
                self.assertEqual(snapshot["room"]["kind"], "floorplan")
                self.assertEqual(len(snapshot["room"]["seats"]), entry["seat_count"])
                for seat in snapshot["room"]["seats"]:
                    physical = source["room"]["layout"]["seats"][seat["label"]]
                    for key in ("x", "y", "width", "height"):
                        self.assertEqual(seat[key], physical[key])
                self.assertTrue(any(f.get("role") == "wall" for f in snapshot["room"]["features"]))
                self.assertTrue(any(f.get("role") == "label" for f in snapshot["room"]["features"]))
                pdf = render_pdf(snapshot)
                reader = self.assert_private(snapshot, pdf)
                self.assertEqual(len(reader.pages), 2)
                self.assertIn("Your seating area", reader.pages[1].extract_text())
                self.assertLess(len(pdf), 600_000)

    def test_recipient_unicode_name_is_also_absent(self):
        source = simple_chart()
        source["students"][0]["name"] = "José Özdemir"
        source["room"]["seats"][0]["student"]["name"] = "José Özdemir"
        snapshot = sanitize_chart(source, "private-enrollment-0")
        reader = PdfReader(io.BytesIO(render_pdf(snapshot)))
        self.assertNotIn("José Özdemir", json.dumps(snapshot, ensure_ascii=False))
        self.assertNotIn("José Özdemir", reader.pages[0].extract_text())


if __name__ == "__main__":
    unittest.main()
