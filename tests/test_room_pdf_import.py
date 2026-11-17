"""Reviewed ILCB identities, physical table groups, and aisle topology."""

import copy
import unittest
from collections import Counter

from room_pdf_import import _profile, parse_pdf_chart, source_index
from room_sources import apply_issues, validate_layout
from rooms import seat_neighbors, spaced_capacity


def chart(room):
    digest = next(key for key, value in source_index().items() if value["room"] == room)
    return copy.deepcopy(_profile(digest))


def neighbors(room):
    seats = chart(room)["seats"]
    return seat_neighbors([{**seat, "id": seat["label"]} for seat in seats])


@unittest.skipUnless(source_index(), "the public copy leaves out the reviewed ILCB plans")
class ReviewedPdfTests(unittest.TestCase):
    def test_all_reviewed_chair_counts_and_complete_geometry(self):
        counts = {111: 356, 112: 104, 113: 605, 205: 204, 207: 192,
                  224: 96, 226: 293, 229: 216, 233: 72, 237: 72}
        self.assertEqual(len(source_index()), 10)
        for room, count in counts.items():
            with self.subTest(room=room):
                result = chart(f"ILCB {room}")
                self.assertEqual(len(result["seats"]), count)
                self.assertEqual(len({s["label"] for s in result["seats"]}), count)
                validate_layout(result["layout"], result["seats"])
                self.assertTrue(any(f.get("role") == "wall" for f in result["layout"]["features"]))
                self.assertTrue(any(f.get("role") == "label" for f in result["layout"]["features"]))

    def test_filename_cannot_substitute_for_verified_source(self):
        with self.assertRaisesRegex(ValueError, "not one of the ten reviewed"):
            parse_pdf_chart(b"%PDF-1.7\nunknown plan", "ILCB 205 - Small Arena Layout.pdf")
        with self.assertRaisesRegex(ValueError, "nonempty"):
            parse_pdf_chart(b"", "room.pdf")
        with self.assertRaisesRegex(ValueError, "Choose a PDF"):
            parse_pdf_chart(b"something", "room.xlsx")

    def test_arena_row_sector_and_table_relationships(self):
        graph = neighbors("ILCB 111")
        self.assertIn("102B", graph["101B"])
        self.assertNotIn("201B", graph["109B"])
        self.assertNotIn("101C", graph["101B"])
        # These four positions are at one actual shared table, so every pair
        # belongs to that table even when not consecutive seat numbers.
        self.assertEqual(graph["104C"], {"105C", "106C", "107C"})
        self.assertEqual(Counter(s["row_label"] for s in chart("ILCB 113")["seats"]),
                         dict(zip("ABCDEFGHJK", [21, 36, 48, 60, 68, 72, 88, 96, 56, 60])))

    def test_fan_aisles_and_pillar_are_not_neighbors(self):
        graph = neighbors("ILCB 226")
        self.assertIn("2A", graph["1A"])
        self.assertNotIn("3A", graph["2A"])
        self.assertNotIn("18M", graph["17M"])
        self.assertEqual(graph["9E"], {f"{n}E" for n in range(10, 17)})

    def test_collaboration_tables_and_continuous_bench_elbows(self):
        for room, capacity in ((207, 84), (224, 42), (229, 34)):
            with self.subTest(room=room):
                self.assertEqual(spaced_capacity(chart(f"ILCB {room}")["seats"]), capacity)
        graph = neighbors("ILCB 207")
        self.assertEqual(graph["D1"], {"D2", "D3"})
        self.assertNotIn("A10", graph["A9"])
        graph = neighbors("ILCB 224")
        self.assertIn("A4", graph["A3"])
        self.assertNotIn("A7", graph["A6"])
        groups = Counter(s["group"] for s in chart("ILCB 229")["seats"])
        self.assertEqual(Counter(groups.values()), {6: 28, 8: 6})
        self.assertEqual(groups["2D"], 8)

    def test_unnumbered_chairs_keep_tables_and_require_review(self):
        for room in (233, 237):
            result = chart(f"ILCB {room}")
            self.assertEqual(Counter(s["group"] for s in result["seats"]),
                             {f"Table {n}": 6 for n in range(1, 13)})
            self.assertTrue(result["review_required"])
            self.assertIn("clockwise", result["review_required"][0])
            positions = result["layout"]["seats"]
            for table in range(1, 13):
                labels = [f"Table {table} · {n}" for n in range(1, 7)]
                top = min(labels, key=lambda label: positions[label]["y"] + positions[label]["height"] / 2)
                self.assertEqual(top, labels[0])
                # The next clockwise chair is to the right of the top chair.
                self.assertGreater(positions[labels[1]]["x"], positions[labels[0]]["x"])

    def test_document_desk_issues_do_not_change_identity_or_cached_profiles(self):
        result = chart("ILCB 111")
        before = {s["label"] for s in result["seats"]}
        apply_issues(result, {"filename": "ILCB 111 Seat Issues.docx", "sha256": "test",
                             "paragraphs": ["ILCB 111 Seat Issues", "104B missing desk",
                                            "Seat 404 on map is actually 405"]}, "ILCB 111")
        self.assertEqual({s["label"] for s in result["seats"]}, before)
        self.assertTrue(next(s for s in result["seats"] if s["label"] == "104B")["broken"])
        self.assertTrue(result["review_required"])
        self.assertFalse(any(s["broken"] for s in chart("ILCB 111")["seats"]))


if __name__ == "__main__":
    unittest.main()
