"""Bubble sheet readings: marks with an unsure band, UIN-then-name matching with confidence, review and export."""
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from collections import Counter
from unittest.mock import patch

import numpy as np
from PIL import Image

from app import create_app
from bubble_fixture import other_page, pdf, sheet
from bubble_sheets import matching, reader
from rosters import connect
from test_rosters import COLUMNS, csv_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
KEY = {number: "ABCDE"[number % 5] for number in range(1, 11)}
STUDENTS = [{"enrollment_id": f"e{index}", "section_id": "s1", "section_number": 501, "uin": uin, "name": name} for index, (uin, name) in enumerate((
    ("123456789", "Example, Alice Marie"), ("223456780", "Example, Bob"), ("987650001", "Nguyen, Carol"), ("555000111", "O'Brien, Dylan James")))]


class ReaderTests(unittest.TestCase):
    def read(self, image, count=10):
        return reader.read_page(np.asarray(image), count)

    def test_marks_versions_orientation_and_a_torn_corner(self):
        for label, image, version in (("plain", sheet(KEY), "A"), ("shifted with a stapled corner", sheet(KEY, "C", shift=(-22, 14), damaged_marker="top_left"), "C"),
                                      ("upside down", sheet(KEY, "B", upside_down=True), "B")):
            with self.subTest(label):
                result = self.read(image)
                self.assertEqual((result["kind"], result["aligned"], result["review"]), ("sheet", True, []))
                self.assertEqual("".join(item["answer"] for item in result["answers"]), "BCDEABCDEA")
                # BUBBLE-SHEETS: a sheet accepted by its marks has the same upright review crops.
                for part in ("id", "name", "version", "q1"):
                    crop = reader.part_image(np.asarray(image), part)
                    self.assertGreater(crop.width, 0)
                    self.assertGreater(crop.height, 0)
                self.assertEqual(({key: result["version"][key] for key in ("state", "answer", "unsure")}, result["last_marked"]), ({"state": "single", "answer": version, "unsure": ""}, 10))
                self.assertGreater(result["version"]["marks"][version], reader.FILLED)  # The strengths are kept, so a later rule can reach this reading.
        self.assertEqual(self.read(sheet(KEY, upside_down=True))["rotation"], 180)
        self.assertNotIn("top_left", self.read(sheet(KEY, damaged_marker="top_left"))["alignment"]["used"])

    def test_anything_unclear_is_reported_never_guessed(self):
        result = self.read(sheet({**KEY, 3: "AD", 5: "", 9: "", 12: "B"}, "", faint={7: "A", 9: "B"}))
        states = {item["question"]: (item["state"], item["answer"], item["unsure"]) for item in result["answers"]}
        # The ghost of an erased A beside a C filled nine times as dark is no second answer. The same faint mark with nothing filled
        # beside it is in doubt: it may be dust on the glass, or a student's light answer, and only the instructor can tell.
        self.assertEqual((states[3], states[5], states[7], states[9], states[1]),
                         (("multiple", "AD", ""), ("blank", "", ""), ("single", "C", ""), ("unsure", "", "B"), ("single", "B", "")))
        # A blank is an answer like any other, a confident double mark is a matter for grading, and a mark past the last question is
        # nobody's answer: none of them is sent to review. What was handed in is still recorded: "AD" for question 3.
        self.assertEqual(result["review"], ["Question 9: a faint or erased mark at B.", "No version bubble is filled."])
        self.assertEqual(result["last_marked"], 12)
        # One rule decides a row, at the read and at every later look. Measured on real sheets: empty bubbles stay under 0.081,
        # light but deliberate marks sit at 0.10 to 0.18.
        self.assertEqual([reader.decide(values)[0] for values in ([0, .16, 0, 0, 0], [0, .12, 0, 0, 0], [0, .05, 0, 0, 0], [0, .37, 0, .09, 0], [0, .2, 0, .16, 0], [0, .3, 0, .13, 0])],
                         ["single", "single", "blank", "single", "multiple", "unsure"])
        # A mark alone in its row counts from 0.10 (the real ones began at 0.105, and nothing empty came near). Below that it may be
        # dust on the glass, and with a second bubble above the empty level the row is compared, not assumed.
        self.assertEqual([reader.decide(values) for values in ([.011, .135, -.002, 0, -.012], [0, .105, .026, -.02, .007], [0, .09, 0, 0, 0], [0, .12, 0, .07, 0])],
                         [("single", "B", ""), ("single", "B", ""), ("unsure", "", "B"), ("unsure", "", "BD")])
        # Bubbles filled about equally dark are a double (or triple) mark, whatever the student meant: recorded, not reviewed. The real
        # ones had a second mark 51% to 97% as dark as the first. A bubble dark enough to count alone yet under 40% of the row's darkest
        # is an answer erased (requirement, 2026-10-25): Exam 2's were 22% to 39% as dark, and every one reviewed kept the dark answer;
        # one at 41% was kept as a double.
        self.assertEqual([reader.decide(values) for values in ([0, .52, 0, .51, 0], [.46, .41, .37, 0, 0], [0, .64, 0, .32, 0], [0, .64, 0, .2, 0],
                                                               [.001, .181, -.008, .515, -.002], [.007, -.005, .141, 0, .642], [.014, -.015, .195, .477, -.001])],
                         [("multiple", "BD", ""), ("multiple", "ABC", ""), ("multiple", "BD", ""), ("single", "B", ""),
                          ("single", "D", ""), ("single", "E", ""), ("multiple", "CD", "")])
        self.assertEqual([reader.question_reason({"question": 3, "state": state, "answer": answer, "unsure": unsure}) for state, answer, unsure in (("multiple", "BD", ""), ("unsure", "B", "D"))],
                         [None, "Question 3: a faint or erased mark at D beside B."])
        # Two fiducials are not enough to place the bubbles.
        torn = np.asarray(sheet(KEY)).copy()
        torn[:300] = 255
        unaligned = reader.read_page(torn, 10)
        self.assertEqual((unaligned.get("aligned"), "answers" in unaligned), (False, False))
        self.assertIn("could not be aligned", unaligned["review"][-1])

    def test_a_sheet_is_recognized_by_its_positioning_marks_either_way_up(self):
        for label, image, rotation in (("plain", sheet(KEY), 0), ("shifted with a stapled corner", sheet(KEY, "C", shift=(-22, 14), damaged_marker="top_left"), 0),
                                       ("upside down", sheet(KEY, "B", upside_down=True), 180), ("unmarked", sheet({}, ""), 0)):
            with self.subTest(label):
                found = reader.detect(np.asarray(image))
                self.assertEqual((found["kind"], found["page"], found["rotation"]), ("sheet", 1, rotation))
                self.assertGreaterEqual(found["markers"], 3)
                self.assertTrue(found["rings"])
        # A stapled corner torn off leaves a real sheet warped: the bubbles sit off their marks, so a read would wait for the
        # instructor, but three well-placed positioning marks still say what the page is.
        warped = sheet(KEY, damaged_marker="top_left")
        inside = warped.crop((150, 260, 1550, 2040))
        warped.paste(255, (150, 260, 1550, 2040))
        warped.paste(inside, (161, 271))
        found = reader.detect(np.asarray(warped))
        self.assertEqual((found["kind"], found["markers"]), ("sheet", 3))
        # Three markers leave no redundancy, and eleven pixels put every thin printed ring off its bubble. The rings themselves are five
        # hundred registration marks: the fit is refined on them, and the sheet reads like any other (two real sheets were lost to this).
        result = reader.read_page(np.asarray(warped), 10)
        self.assertEqual((result["aligned"], result["alignment"]["refined"], result["review"], "".join(item["answer"] for item in result["answers"])), (True, True, [], "BCDEABCDEA"))
        self.assertFalse(reader.read_page(np.asarray(sheet(KEY)), 10)["alignment"]["refined"])  # A fit the rings confirm is left exactly as it was.
        self.assertEqual(reader.detect(np.asarray(other_page()))["kind"], "other")
        self.assertEqual(reader.detect(np.full((2200, 1700), 255, dtype=np.uint8))["kind"], "other")
        self.assertEqual(reader.detect(np.zeros((1100, 850), dtype=np.uint8))["kind"], "other")  # Not the 200-DPI Letter raster: never guessed.

    def test_marks_and_rings_settle_a_sheet_whose_resemblance_was_missed_or_taken_for_the_second_page(self):
        """requirement, 2026-09-25: two plain sheets of one scan were called 'not bubble sheets'. That scan's sheets resembled the
        template at only 0.22 to 0.25; one fell just below 0.22, and another resembled the template's second page by a hair."""
        missed = {"kind": "other", "page": 1, "rotation": 0, "score": .217}
        second = {"kind": "unsure", "page": 2, "rotation": 0, "score": .225}
        for label, image, proposal, rotation in (("below the resemblance", sheet(KEY, "A"), missed, 0), ("taken for page 2", sheet(KEY, "A"), second, 0),
                                                 ("upside down and missed", sheet(KEY, "B", upside_down=True), missed, 180)):
            with self.subTest(label), patch.object(reader, "classify", return_value=dict(proposal)):
                found = reader.detect(np.asarray(image))
                self.assertEqual((found["kind"], found["page"], found["rotation"], found["settled_by"]), ("sheet", 1, rotation, "marks and rings"))
                self.assertGreaterEqual(found["ring"], reader.RING_SURE)
                # The read settles it the same way, so the page is read, and read as the first page, the right way up.
                result = self.read(image)
                self.assertEqual((result["kind"], result["page"], result["aligned"], result["review"]), ("sheet", 1, True, []))
                self.assertEqual("".join(item["answer"] for item in result["answers"]), "BCDEABCDEA")
        # No other page has the positioning marks and five hundred rings in their places: it stays what it was.
        with patch.object(reader, "classify", return_value=dict(missed)):
            self.assertEqual(reader.detect(np.asarray(other_page()))["kind"], "other")
            self.assertEqual(self.read(other_page())["kind"], "other")
        with patch.object(reader, "classify", return_value=dict(second)):
            self.assertEqual(reader.detect(np.asarray(other_page()))["kind"], "unsure")
        # A sheet the resemblance already recognizes is recognized exactly as before, without asking the marks again.
        self.assertNotIn("settled_by", reader.detect(np.asarray(sheet(KEY))))

    def test_a_middling_page_resemblance_is_settled_by_clear_alignment(self):
        # Bold marks and a full name box lower the whole-page resemblance of the cleanest scans (0.25 to 0.34 on seven real sheets,
        # every one of them aligned far better than needed). The positioning marks and the rings decide, so nothing is flagged.
        unsure = {"kind": "unsure", "page": 1, "rotation": 0, "score": .29}
        with patch.object(reader, "classify", return_value=dict(unsure)):
            result = self.read(sheet(KEY))
        self.assertEqual((result["kind"], result["aligned"], result["review"]), ("unsure", True, []))
        self.assertGreaterEqual(result["alignment"]["ring"], reader.RING_SURE)
        self.assertEqual("".join(item["answer"] for item in result["answers"]), "BCDEABCDEA")

    def test_the_name_and_id_crops_follow_the_handwriting_not_just_the_printed_box(self):
        from PIL import ImageDraw
        page = reader.template()["pages"][0]
        name, ident = page["fields"]["name"], page["fields"]["id"]
        unwritten = self.read(sheet(KEY))["fields"]["name"]
        self.assertTrue(all(abs(found - printed) <= 2 for found, printed in zip(unwritten, name)), (unwritten, name))  # Nothing written: the printed box.

        def written(rows_by_field):
            image = sheet(KEY)
            draw = ImageDraw.Draw(image)
            for box, (top, bottom) in rows_by_field:
                for x in range(box[0] + 40, box[0] + 420, 26):  # strokes like handwriting: tall, separate, never edge to edge
                    draw.line([(x, top), (x + 12, bottom)], fill=40, width=4)
            return self.read(image)["fields"]

        # Many students write on the rule or below it. A crop of the printed box alone would slice this writing in half (several
        # real sheets stayed unidentified for exactly that); the crop follows the band of ink, whole, and never shrinks.
        low = written([(name, (name[3] - 30, name[3] + 28))])["name"]
        self.assertTrue(low[1] <= name[3] - 30 - 4 and low[3] >= name[3] + 28 + 4 and low[3] - low[1] >= name[3] - name[1], (low, name))
        self.assertTrue(abs(low[0] - name[0]) <= 2 and abs(low[2] - name[2]) <= 2, (low, name))  # Only the rows move.
        # Writing in the row below is the ID's, not the name's: each crop keeps to its own band.
        both = written([(name, (name[1] + 20, name[3] - 12)), (ident, (ident[1] + 30, ident[3] + 20))])
        self.assertTrue(both["name"][3] < ident[1] + 30 and both["id"][1] > name[3] - 12 and both["id"][3] >= ident[3] + 20, both)
        # Two rows' writing running into each other cannot swallow the page: at most 1.7 of the printed height.
        merged = written([(name, (name[1] - 60, ident[3] + 60))])["name"]
        self.assertLessEqual(merged[3] - merged[1], 1.7 * (name[3] - name[1]) + 1)

    def test_click_targets_lie_over_the_bubbles_of_every_snapshot(self):
        targets, page = reader.choice_targets(), reader.template()["pages"][0]
        self.assertEqual([[zone["letter"] for zone in targets[kind]] for kind in ("question", "version")], [list("ABCDE")] * 2)
        rows = [(row, 62, "question") for row in page["questions"].values()] + [(page["version"], 150, "version")]
        self.assertEqual(len(rows), 101)
        for row, lead, kind in rows:
            left, right = row[0][0] - lead, row[-1][0] + row[0][2] / 2 + 12
            for (x, _, diameter), zone in zip(row, targets[kind]):
                # The whole printed bubble sits inside its letter's zone, and zones never overlap.
                start, end = (x - diameter / 2 - left) / (right - left) * 100, (x + diameter / 2 - left) / (right - left) * 100
                self.assertTrue(zone["left"] <= start and end <= zone["left"] + zone["width"] + .01, (kind, zone, start, end))
        for kind in targets:
            edges = [(zone["left"], zone["left"] + zone["width"]) for zone in targets[kind]]
            self.assertTrue(all(a[1] <= b[0] + .02 for a, b in zip(edges, edges[1:])) and edges[-1][1] <= 100, edges)

    def test_a_sheet_a_home_scanner_cropped_to_the_paper_edge_reads_as_a_letter_one(self):
        # 2026-10-24: the home scanners crop every page to the paper's edge (the Epson's 1675 to 1695 by 2195 to 2220 pixels at 200 DPI);
        # only exact Letter rasters were looked at, so no home-scanned bubble sheet was recognized.
        for size in ((1684, 2206), (1675, 2195), (1695, 2220)):
            with self.subTest(size):
                image = sheet(KEY, "B").resize(size, Image.BICUBIC)
                self.assertEqual(reader.detect(np.asarray(image))["kind"], "sheet")
                result = self.read(image)
                self.assertEqual((result["kind"], result["aligned"], result["review"]), ("sheet", True, []))
                self.assertEqual(("".join(item["answer"] for item in result["answers"]), result["version"]["answer"]), ("BCDEABCDEA", "B"))
                self.assertGreater(reader.part_image(np.asarray(image), "name").width, 0)
        self.assertEqual(reader.detect(np.asarray(other_page().resize((1684, 2206))))["kind"], "other")
        # Further off than a trimmed edge (here a 150-DPI page) is no sheet, as before.
        self.assertEqual(reader.detect(np.asarray(sheet(KEY).resize((1275, 1650))))["kind"], "other")

    def test_other_pages_and_wrong_rasters(self):
        self.assertEqual({key: self.read(other_page())[key] for key in ("kind", "review")}, {"kind": "other", "review": []})
        with self.assertRaises(ValueError):
            reader.read_page(np.zeros((1100, 850), dtype=np.uint8), 10)


class SeatVersionTests(unittest.TestCase):
    """requirement, 2026-10-24: 94 of 109 Exam 2 sheets had no version bubbled. A sheet its Scantron label names takes its
    student's version from the seating chart, as the booklet does; a letter bubbled against the chart is still asked."""
    def current(self, version, method="label", seats={("s1", "e1"): {"B"}}, booklets={}):
        from bubble_sheets.service import BubbleSheetService
        item = {"reading": reader.read_page(np.asarray(sheet(KEY, version)), 10),
                "match": {"state": "matched", "method": method, "enrollment_id": "e1", "section_id": "s1", "confidence": 1.0, "notes": []}}
        return BubbleSheetService._current(item, None, {"threshold": .85, "yields": False, "duplicate": None, "taken": set(), "seats": seats,
                                                        "booklets": booklets})

    def test_the_booklets_settled_version_comes_before_the_seat(self):
        """THE BOOKLET'S VERSION (requirement, 2026-10-25): 68 of 312 Exam 2 booklets were another version than their seat's, and 57
        sheets with no version bubbled were scored against the seat's key."""
        booklet = {("s1", "e1"): "A"}  # seated for B, but page 1 of the student's booklet reads A
        blank = self.current("", booklets=booklet)
        self.assertEqual((blank["version"], blank.get("version_from"), blank["status"], blank["flags"]["version"]), ("A", "booklet", "clean", False))
        self.assertEqual((self.current("A", booklets=booklet)["version"], self.current("A", booklets=booklet)["status"]), ("A", "clean"))
        against = self.current("B", booklets=booklet)  # the seat's letter bubbled against the booklet is asked
        self.assertEqual((against["status"], against["flags"]["version"]), ("needs_review", True))
        self.assertTrue(any(reason.endswith("this student's exam booklet is Version A.") for reason in against["reasons"]), against["reasons"])
        # A booklet still being confirmed leaves the seat's rule as it was; a sheet named by its UIN takes neither.
        self.assertEqual(self.current("", booklets={})["version"], "B")
        self.assertEqual(self.current("", method="uin", booklets=booklet)["version"], "")

    def test_a_labelled_sheet_takes_its_version_from_the_seating_chart(self):
        blank = self.current("")
        self.assertEqual((blank["version"], blank.get("version_from"), blank["status"], blank["reasons"], blank["flags"]["version"]),
                         ("B", "seating_chart", "clean", [], False))
        self.assertEqual((self.current("B")["version"], self.current("B")["status"]), ("B", "clean"))
        both = self.current("AB")  # two letters, one of them the chart's
        self.assertEqual((both["version"], both["status"]), ("B", "clean"))
        for bubbled, said in (("A", "Version A is bubbled"), ("AC", "Version A and C is bubbled")):
            against = self.current(bubbled)
            self.assertEqual((against["status"], against["flags"]["version"]), ("needs_review", True))
            self.assertTrue(any(reason.startswith(said) and reason.endswith("gave this student Version B.") for reason in against["reasons"]))
        # Only a sheet its label names, and only a student the chart gives one version.
        for item in (self.current("", method="uin"), self.current("", seats={}), self.current("", seats={("s1", "e1"): {"A", "B"}})):
            self.assertEqual((item["version"], item["status"], item["reasons"]), ("", "needs_review", ["No version bubble is filled."]))


class MatchingTests(unittest.TestCase):
    def test_uin_first_then_name_with_a_stated_confidence(self):
        exact = matching.match("UIN: 123 456 789", "alice example", STUDENTS)
        self.assertEqual((exact["state"], exact["method"], exact["enrollment_id"], exact["section_id"]), ("matched", "uin", "e0", "s1"))
        self.assertGreaterEqual(exact["confidence"], .95)
        # Stray digits around the UIN, and no readable name, still match exactly; the name only adds confidence.
        self.assertEqual((matching.match("1D 9876500011", "", STUDENTS)["state"], matching.match("987650001", "", STUDENTS)["confidence"]), ("matched", .85))
        # One digit misread: not exact, so it is a suggestion: 55% of the UIN similarity plus 45% of the name similarity (the
        # instructor's chosen rule). At 85% or more it identifies the student; below that it waits for the instructor.
        near = matching.match("123456189", "Alice Example", STUDENTS)
        self.assertEqual((near["state"], near["method"], near["enrollment_id"], near["confidence"], matching.identified(near)), ("suggested", "uin+name", "e0", .939, True))
        # The threshold is the instructor's setting: 70% unless changed.
        weaker = matching.match("987650117", "Karol Nguen", STUDENTS)
        self.assertEqual((weaker["state"], weaker["enrollment_id"], weaker["confidence"], matching.identified(weaker), matching.identified(weaker, .85)), ("suggested", "e2", .752, True, False))
        waiting = matching.match("987651117", "Karol Nguen", STUDENTS)
        self.assertEqual((waiting["state"], waiting["confidence"], matching.identified(waiting)), ("suggested", .691, False))
        # The lead over the runner-up only breaks near-ties. A student shown at 73% with the next at 66% is suggested, so the
        # instructor's threshold decides; two students within five points of each other are said to be so, and nobody is suggested.
        classmates = [{"enrollment_id": "c0", "section_id": "s1", "section_number": 501, "uin": "900000639", "name": "Bridges, Casey"},
                      {"enrollment_id": "c1", "section_id": "s1", "section_number": 501, "uin": "900000739", "name": "Brooks, Corey"},
                      {"enrollment_id": "c2", "section_id": "s1", "section_number": 501, "uin": "538001239", "name": "Zamora, Ines"}]
        clear = matching.match("900000689", "Casy Bridgs", classmates)
        self.assertEqual((clear["state"], clear["enrollment_id"]), ("suggested", "c0"))
        self.assertGreaterEqual(clear["confidence"] - clear["candidates"][1]["confidence"], .05)
        tie = matching.match("900000039", "", classmates)  # One digit from each of two classmates, and no name to tell them apart.
        self.assertEqual((tie["state"], tie["enrollment_id"]), ("unmatched", None))
        self.assertRegex(tie["notes"][0], r"Two students are almost equally close to what was read: .+ \(\d+%\) and .+ \(\d+%\)\.")
        # No digits at all: the name alone can suggest, never match.
        named = matching.match("", "Dylan O Brien", STUDENTS)
        self.assertEqual((named["state"], named["method"], named["enrollment_id"]), ("suggested", "name", "e3"))
        # A poor read of both suggests nobody; the nearest students are still listed.
        unknown = matching.match("98", "zz", STUDENTS)
        self.assertEqual((unknown["state"], unknown["enrollment_id"], len(unknown["candidates"])), ("unmatched", None, 3))
        self.assertEqual(matching.match("", "", STUDENTS)["notes"], ["Neither the ID nor the name could be read."])
        self.assertEqual(matching.match("123456789", "x", [])["state"], "unmatched")

    def test_a_uin_typed_into_a_student_picker_finds_the_closest_students_by_name_only(self):
        found = matching.uin_search("123 456 789", STUDENTS)
        self.assertEqual((found[0]["name"], found[0]["similarity"]), ("Example, Alice Marie", 1.0))
        self.assertTrue(all("uin" not in item for item in found))
        # Part of a UIN is compared with the stretch of each UIN it matches best; one wrong digit still finds the student.
        self.assertEqual([item["name"] for item in matching.uin_search("98765", STUDENTS)][:1], ["Nguyen, Carol"])
        self.assertEqual(matching.uin_search("55500x0111", STUDENTS)[0]["name"], "O'Brien, Dylan James")
        self.assertEqual(matching.uin_search("223456781", STUDENTS)[0]["name"], "Example, Bob")
        # Too few digits say nothing.
        self.assertEqual(matching.uin_search("123", STUDENTS), [])

    def test_a_scantron_label_matches_unless_an_exact_uin_read_is_someone_elses(self):
        self.assertEqual({key: matching.match("", "", STUDENTS, "e2")[key] for key in ("state", "method", "enrollment_id", "confidence", "notes")},
                         {"state": "matched", "method": "label", "enrollment_id": "e2", "confidence": 1.0, "notes": []})
        self.assertEqual(matching.match("987650001", "Carol Nguyen", STUDENTS, "e2")["state"], "matched")
        clash = matching.match("223456780", "", STUDENTS, "e2")
        self.assertEqual((clash["state"], clash["enrollment_id"], clash["confidence"], clash["candidates"][1]["enrollment_id"]), ("suggested", "e2", .5, "e1"))
        outside = matching.match("123456789", "", STUDENTS, "e9")
        self.assertEqual((outside["state"], outside["enrollment_id"], outside["notes"]),
                         ("matched", "e0", ["The sheet's Scantron label belongs to a student outside the chosen sections."]))

    def test_a_name_that_points_elsewhere_keeps_an_exact_uin_from_matching_silently(self):
        clash = matching.match("223456780", "Carol Nguyen", STUDENTS)
        self.assertEqual((clash["state"], clash["enrollment_id"], clash["confidence"]), ("suggested", "e1", .5))
        self.assertIn("the name read looks like Nguyen, Carol", clash["notes"][0])
        # Roster UINs never appear in a result.
        self.assertNotIn("223456780", repr(clash))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.inbox, self.scans, self.bubbles = (self.app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets"))
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502], "multiple_sections": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        state = self.client.get("/api/state").json
        rows = [["", student["name"], student["uin"], "Registered", "", "", "", "", "", "", "", f"s{index}@example.test"] for index, student in enumerate(STUDENTS)]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/501/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        self.sections = {item["number"]: item["id"] for item in self.client.get("/api/seating/catalog").json["sections"]}

    def scan(self, images):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": pdf(images)}], source="email", sender="scanner",
                                idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        self.assertTrue(self.scans.process_one())
        return job["message_id"]

    def read(self, message, texts, **overrides):
        body = {"assignment_id": self.assignment["id"], "section_ids": [self.sections[501]], "question_count": 10, **overrides}
        queued = self.client.post(f"/api/bubble-sheets/messages/{message}", json=body, headers=HEADERS)
        if queued.status_code != 202:
            return queued
        ocr = lambda content, boxes: {"reads": [{"text": text} for pair in texts for text in pair][:len(boxes)], "seconds": 0}
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
            self.assertTrue(self.bubbles.process_one())
        return self.client.get(f"/api/bubble-sheets/messages/{message}")

    def test_sheets_are_recognized_on_arrival_but_whose_they_are_stays_the_instructors_choice(self):
        self.scans.alias_ocr.enabled = True
        message = self.scan([sheet(KEY, "A"), other_page(), sheet(KEY, "B", upside_down=True)])
        recognition = self.scans.review_item(message)["recognition"]
        self.assertEqual((recognition["verdict"], recognition["papers"], recognition["without_label"]), ("bubble_sheets", [], 3))
        self.assertEqual(recognition["bubble_sheets"], {"template": "Gradescope Bubble Sheet v2020.05.01", "sheets": 2, "unsure": 0, "checked": 3, "pending": 0, "read": None})
        self.assertTrue(self.scans.review_item(message)["instructor_review_required"])
        with connect(self.app.config["DATABASE"]) as db:
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='bubble_sheets_recognized'").fetchone()[0])
            # A sheet was never issued a label or alias, so printed-label OCR looks only at the page that is not one.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_alias_ocr").fetchone()[0], 1)
            with self.assertRaises(Exception):
                db.execute("DELETE FROM bubble_detections")
        self.assertEqual((event["sheets"], event["pages_checked"], event["associated"], event["instructor_review_required"]), (2, 3, False, True))
        summary = self.client.get(f"/api/bubble-sheets/messages/{message}").json
        self.assertEqual((summary["detected"], summary["run"]), ({"sheets": 2, "unsure": 0, "checked": 3, "pending": 0}, None))
        waiting = self.scans.review_item(message)["overview"]
        self.assertEqual((waiting["headline"], waiting["state"]), ("2 bubble sheets", "todo"))
        self.assertEqual(waiting["steps"][0]["action"], "grading_associate")
        self.assertTrue(any(step["tool"] == "bubble" for step in waiting["steps"]))
        # Naming the assignment and sections is what associates them; the recognition then says so.
        self.assertEqual(self.read(message, [("123456789", "Alice Example"), ("223456780", "Bob Example")]).status_code, 200)
        self.assertEqual(self.scans.review_item(message)["recognition"]["bubble_sheets"]["read"],
                         {"title": "Test 1", "status": "complete", "sections": [501], "counts": {"clean": 2, "needs_review": 0, "reviewed": 0}})
        # Nothing labeled was ever scanned here, so the form has no assignment to start from. The form offers the seating catalog's
        # assignments and sections but no room, so the panel never reads the rooms (their spaced capacity is most of the catalog's cost).
        with patch.object(self.app.extensions["seating"].rooms, "list", side_effect=AssertionError("rooms read for the bubble sheet panel")):
            panel = self.client.get(f"/api/bubble-sheets/messages/{message}")
        self.assertEqual(panel.status_code, 200)
        self.assertIsNone(panel.json["suggested_assignment_id"])
        catalog = self.client.get("/api/seating/catalog").json
        self.assertEqual((panel.json["assignments"], panel.json["sections"], "rooms" in panel.json), (catalog["assignments"], catalog["sections"], False))
        # The Inbox lists the scan by the same summary its page opens with: what it holds and what is left, without a student's name.
        listed = next(job for job in self.client.get("/api/inbox/jobs").json["jobs"] if job["message_id"] == message)["scan"]
        self.assertEqual((listed["headline"], listed["state"], listed["facts"]),
                         ("2 bubble sheets · Test 1 · Section 501", "todo", ["3 pages"]))
        self.assertEqual(listed["steps"][0]["action"], "grading_review")
        self.assertTrue(any(step["text"] == "All 2 bubble sheets read for Test 1 · Section 501." for step in listed["steps"]))
        self.assertEqual(listed, self.scans.review_item(message)["overview"])
        # A scan with no label and no sheet is still not recognized, and nothing is looked at twice.
        other = self.scan([other_page(), other_page()])
        self.assertEqual(self.scans.review_item(other)["recognition"]["verdict"], "unrecognized")
        self.assertFalse(self.bubbles.process_one())

    def test_the_inbox_files_a_scan_under_the_tab_its_own_summary_speaks_for(self):
        # A retained scan keeps `needs_review` for good: automation never clears the instructor review gate. Its place in the list
        # follows what is actually left to do, so "Nothing to do" is not found under Needs review.
        message = self.scan([sheet(KEY, "A")])
        listing = lambda status="": self.client.get(f"/api/inbox/jobs?status={status}").json
        waiting = listing()
        row = next(job for job in waiting["jobs"] if job["message_id"] == message)
        self.assertEqual((row["status"], row["scan"]["state"], row["listed_as"], waiting["counts"]), ("needs_review", "todo", "needs_review", {"needs_review": 1}))
        self.assertEqual([job["message_id"] for job in listing("needs_review")["jobs"]], [message])
        self.assertEqual(listing("history")["total"], 0)
        self.assertEqual(self.read(message, [("123456789", "Alice Example")]).status_code, 200)
        # Reading and association leave the explicit scan review outstanding.
        self.assertTrue(self.scans.review_item(message)["instructor_review_required"])
        self.assertEqual(listing()["counts"], {"needs_review": 1})
        grading = self.app.extensions["frq_grading"]
        state = grading.scan_context(message)
        grading.review_scan(message, {"fingerprint": state["fingerprint"], "confirm_review": True})
        done = listing()
        row = next(job for job in done["jobs"] if job["message_id"] == message)
        self.assertEqual((row["status"], row["scan"]["state"], row["listed_as"], done["counts"]), ("needs_review", "done", "completed", {"completed": 1}))
        self.assertEqual(([job["message_id"] for job in listing("completed")["jobs"]], listing("completed")["total"], listing("needs_review")["jobs"], listing("needs_review")["total"]),
                         ([message], 1, [], 0))
        self.assertEqual(([job["message_id"] for job in listing("history")["jobs"]], listing("history")["total"]), ([message], 1))
        # Only the explicit instructor review clears the gate.
        self.assertFalse(self.scans.review_item(message)["instructor_review_required"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT status FROM inbox_jobs WHERE message_id=?", (message,)).fetchone()[0], "needs_review")

    def test_scans_inspected_before_sheets_were_recognized_are_caught_up_by_the_worker(self):
        self.scans.bubble_detector = None  # As it was: inspection did not look for sheets.
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B")])
        self.scans.bubble_detector = self.bubbles
        before = self.scans.review_item(message)["recognition"]
        self.assertEqual((before["verdict"], before["bubble_sheets"]["pending"], before["bubble_sheets"]["checked"]), ("unrecognized", 2, 0))
        self.assertTrue(self.bubbles.process_one())
        after = self.scans.review_item(message)["recognition"]
        self.assertEqual((after["verdict"], after["bubble_sheets"]["sheets"], after["bubble_sheets"]["pending"]), ("bubble_sheets", 2, 0))
        self.assertFalse(self.bubbles.process_one())

    def test_marks_5_keeps_what_marks_4_looked_at_and_looks_again_at_what_it_passed_over_for_its_size(self):
        # BUBBLE-SHEETS "HOME-SCANNED SHEETS": marks-4 recorded every home-scanned page as `other` without looking at it.
        from bubble_sheets.service import BubbleSheetService
        self.scans.bubble_detector = None
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B"), other_page()])
        self.scans.bubble_detector = self.bubbles
        skipped = json.dumps({"kind": "other", "markers": 0, "page": None, "ring": None, "rotation": 0, "score": 0.0})
        with connect(self.app.config["DATABASE"]) as db:
            pages = [row[0] for row in db.execute("SELECT id FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (message,))]
            db.executemany("INSERT INTO bubble_detections VALUES(?,?,?,?,?,'t')", [
                (pages[0], f"{reader.PROFILE}/marks-4", message, "sheet", json.dumps({"kind": "sheet", "score": .5, "ring": .2})),
                (pages[1], f"{reader.PROFILE}/marks-4", message, "other", skipped),
                (pages[2], f"{reader.PROFILE}/marks-4", message, "other", json.dumps({"kind": "other", "score": .05, "ring": None}))])
        BubbleSheetService(self.scans, self.bubbles.seating)  # as at a restart
        with connect(self.app.config["DATABASE"]) as db:
            carried = dict(db.execute("SELECT scan_page_id,kind FROM bubble_detections WHERE detector=?", (f"{reader.PROFILE}/marks-5",)).fetchall())
        self.assertEqual(carried, {pages[0]: "sheet", pages[2]: "other"})
        self.assertTrue(self.bubbles.process_one())  # only the page passed over is looked at, and it is a sheet
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(self.bubbles.sheet_pages(db, message), {pages[0], pages[1]})

    def test_a_scan_is_read_reviewed_and_exported_without_touching_the_review_gate(self):
        message = self.scan([sheet(KEY, "A"), other_page(), sheet({**KEY, 4: ""}, "B", faint={4: "D"}), sheet(KEY, "A"), sheet(KEY, "B")])
        for bad in ({"question_count": 0}, {"question_count": 101}, {"section_ids": ["missing"]}, {"assignment_id": "missing"}):
            self.assertEqual(self.read(message, [], **bad).status_code, 400, bad)
        self.assertEqual(self.client.post("/api/bubble-sheets/messages/missing", json={}, headers=HEADERS).status_code, 404)
        result = self.read(message, [("123456789", "Alice Example"), ("223456180", "Bob Example"), ("", "scribble"), ("123456789", "Alice M Example")]).json
        run = result["run"]
        self.assertEqual((run["status"], run["title"], run["sections"], run["question_count"]), ("complete", "Test 1", [501], 10))
        self.assertEqual({key: run["summary"][key] for key in ("pages", "sheets", "matched", "suggested", "unmatched", "mark_review", "usual_last_question", "students")},
                         {"pages": 5, "sheets": 4, "matched": 2, "suggested": 1, "unmatched": 1, "mark_review": 1, "usual_last_question": 10, "students": 4})
        by_page = {item["page"]: item for item in run["sheets"]}
        self.assertEqual(sorted(by_page), [1, 3, 4, 5])  # The exam page between them is not a sheet.
        # Two sheets claim Alice by exact UIN: both wait for the instructor rather than one silently winning.
        self.assertEqual([(by_page[page]["status"], by_page[page]["student"]) for page in (1, 5)], [("needs_review", "Example, Alice Marie")] * 2)
        self.assertIn("Another sheet in this scan is recorded for the same student.", by_page[1]["reasons"])
        # One digit of Bob's UIN was misread, but the near UIN and his name agree at 94%: at 85% or more the student is identified
        # without being asked (the instructor's standing request). The sheet still waits, for its faint mark alone.
        self.assertEqual((by_page[3]["match"], by_page[3]["identified_by"], by_page[3]["confidence"], by_page[3]["student"], by_page[3]["answers"], by_page[3]["version"]),
                         ("suggested", "confidence", .939, "Example, Bob", "BCD·ABCDEA", "B"))
        self.assertTrue(any("faint or erased mark at D" in reason for reason in by_page[3]["reasons"]) and not any("suggestion" in reason for reason in by_page[3]["reasons"]))
        self.assertEqual((run["identity"], run["auto_identify"]), ({"uin": 2, "confidence": 1, "review": 0, "none": 1}, .7))
        self.assertEqual((by_page[4]["match"], by_page[4]["status"]), ("unmatched", "needs_review"))
        self.assertEqual(run["counts"], {"clean": 0, "needs_review": 4, "reviewed": 0})
        # The machine reading never changes; a review records the instructor's values over it.
        detail = self.client.get(f"/api/bubble-sheets/sheets/{by_page[3]['id']}").json
        self.assertEqual((detail["question_count"], detail["match"]["candidates"][0]["name"], [s["name"] for s in detail["students"]][:2]),
                         (10, "Example, Bob", ["Example, Alice Marie", "Example, Bob"]))
        self.assertNotIn("uin", detail["students"][0])
        # Its picker finds students by a typed UIN on the server, from this read's own roster; names and sections come back only.
        found = self.client.post(f"/api/bubble-sheets/sheets/{by_page[3]['id']}/students", json={"uin": "223456780"}, headers=HEADERS)
        self.assertEqual((found.status_code, found.json["students"][0]["name"], found.json["students"][0]["similarity"]), (200, "Example, Bob", 1.0))
        self.assertNotIn("223456780", found.get_data(as_text=True))
        self.assertEqual(self.client.post(f"/api/bubble-sheets/sheets/{by_page[3]['id']}/students", json={"uin": 5}, headers=HEADERS).status_code, 400)
        # The review opens only what is in doubt: this sheet's faint question 4, not its identified student or its version.
        self.assertEqual(detail["current"]["flags"], {"identity": False, "version": False, "questions": [4], "unread": False})
        clear = self.client.get(f"/api/bubble-sheets/sheets/{by_page[4]['id']}").json["current"]["flags"]
        self.assertEqual(clear, {"identity": True, "version": False, "questions": [], "unread": False})
        review = lambda sheet_id, **data: self.client.post(f"/api/bubble-sheets/sheets/{sheet_id}/review", json=data, headers=HEADERS)
        answers = list("BCDDABCDEA")
        for bad in ({"answers": answers[:9]}, {"answers": ["F"] + answers[1:]}, {"answers": ["DA"] + answers[1:]}, {"answers": answers, "version": "AB"},
                    {"answers": answers, "enrollment_id": "nobody", "section_id": self.sections[501]}, {"answers": answers, "enrollment_id": detail["students"][1]["enrollment_id"]}):
            self.assertEqual(review(by_page[3]["id"], **bad).status_code, 400, bad)
        bob = detail["students"][1]
        saved = review(by_page[3]["id"], answers=answers, version="B", section_id=bob["section_id"], enrollment_id=bob["enrollment_id"], note="Erased D, kept D")
        self.assertEqual((saved.status_code, saved.json["current"]["status"], saved.json["current"]["answers"][3], saved.json["reading"]["answers"][3]["state"]), (201, "reviewed", "D", "unsure"))
        self.assertEqual(saved.json["current"]["flags"], {"identity": False, "version": False, "questions": [], "unread": False})  # Once reviewed, nothing is in doubt.
        # The duplicate is settled by recording page 5 for nobody; page 1 then stands as read.
        alice = detail["students"][0]
        self.assertEqual(review(by_page[5]["id"], answers=list("BCDEABCDEA"), version="B").status_code, 201)
        after = {item["page"]: item for item in self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]["sheets"]}
        self.assertEqual([(after[page]["status"], after[page]["student"]) for page in (1, 3, 5)], [("clean", alice["name"]), ("reviewed", "Example, Bob"), ("reviewed", "")])
        export = self.client.get(f"/api/bubble-sheets/runs/{run['id']}/export.csv")
        rows = list(csv.reader(io.StringIO(export.data.decode("utf-8-sig"))))
        self.assertEqual((export.status_code, rows[0][:7], rows[0][-1], len(rows)), (200, ["Scan page", "Status", "Name", "UIN", "Section", "Identified by", "Version"], "Needs review", 5))
        self.assertEqual(rows[1][:7], ["1", "clean", "Example, Alice Marie", "123456789", "501", "exact UIN", "A"])
        self.assertEqual(rows[2][:7] + rows[2][7:17], ["3", "reviewed", "Example, Bob", "223456780", "501", "instructor", "B", *answers])
        self.assertEqual((rows[4][:7], rows[4][-1]), (["5", "reviewed", "", "", "", "", "B"], ""))  # Empty cells stay empty.
        self.assertIn("Test 1 - bubble sheet readings.csv", export.headers["Content-Disposition"])
        self.assertTrue(self.scans.review_item(message)["instructor_review_required"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox_events WHERE kind IN ('bubble_sheets_requested','bubble_sheets_read','bubble_sheet_reviewed')").fetchone()[0], 4)
            for statement in ("UPDATE bubble_sheets SET id_text='x'", "DELETE FROM bubble_reviews", "UPDATE bubble_reviews SET version='A'"):
                with self.assertRaises(Exception):
                    db.execute(statement)

    def test_each_question_is_shown_as_the_piece_of_the_scan_it_came_from(self):
        from PIL import Image
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "C", upside_down=True, shift=(10, -8))])
        upright, flipped = self.read(message, [("123456789", "Alice Example"), ("223456780", "Bob Example")]).json["run"]["sheets"]
        page = reader.template()["pages"][0]

        def crop(sheet_id, part):
            response = self.client.get(f"/api/bubble-sheets/sheets/{sheet_id}/parts/{part}")
            self.assertEqual((response.status_code, response.mimetype), (200, "image/png"), part)
            return np.asarray(Image.open(io.BytesIO(response.data)).convert("L"))

        def filled_at(picture, row, margin):
            """Which of the row's five bubbles holds the dark blob, from where it sits across the crop."""
            columns = np.nonzero((picture < 80).sum(axis=0) > picture.shape[0] * .3)[0]
            centre = columns.mean() / picture.shape[1] * (row[-1][0] + row[-1][2] / 2 + 12 - (row[0][0] - margin)) + row[0][0] - margin
            return "ABCDE"[int(np.argmin([abs(centre - x) for x, _, _ in row]))]

        for sheet_id, version in ((upright["id"], "A"), (flipped["id"], "C")):  # The upside-down sheet is shown the right way up.
            self.assertEqual([filled_at(crop(sheet_id, f"q{number}"), page["questions"][str(number)], 62) for number in (1, 2, 5, 10)], ["B", "C", "A", "A"])
            self.assertEqual(filled_at(crop(sheet_id, "version"), page["version"], 150), version)
            for part in ("id", "name"):
                picture = crop(sheet_id, part)
                self.assertGreater(picture.shape[1], picture.shape[0] * 4)  # A wide handwriting box, enlarged.
                self.assertGreater(picture.shape[1], 500)
        for bad in ("q0", "q101", "q01", "q1a", "answers"):
            self.assertEqual(self.client.get(f"/api/bubble-sheets/sheets/{upright['id']}/parts/{bad}").status_code, 400, bad)
        self.assertEqual(self.client.get("/api/bubble-sheets/sheets/missing/parts/q1").status_code, 404)

    def test_a_suggestion_at_85_percent_identifies_the_student_and_a_weaker_one_waits(self):
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "A"), sheet({**KEY, 2: "AB"}, "A"), sheet(KEY, "B")])
        # The threshold is the instructor's adjustable setting, 70% until changed; it applies at once, to reads already made too.
        setting = lambda value: self.client.post("/api/bubble-sheets/settings", json={"auto_identify": value}, headers=HEADERS)
        self.assertEqual([setting(bad).status_code for bad in (.5, 1.2, "high", None, True)], [400] * 5)
        self.assertEqual((setting(.85).status_code, setting(.85).json), (200, {"auto_identify": .85}))
        run = self.read(message, [("123456189", "Alice Example"), ("987650117", "Karol Nguen"), ("223456180", "Bob Example"), ("", "Dylan OBrien")]).json["run"]
        sheets = {item["page"]: item for item in run["sheets"]}
        # 94%: identified, nothing else in doubt, so the sheet is simply read. 75%: under 85%, still only a suggestion for the instructor.
        self.assertEqual([(sheets[page]["status"], sheets[page]["identified_by"], sheets[page]["student"], sheets[page]["confidence"]) for page in (1, 2)],
                         [("clean", "confidence", "Example, Alice Marie", .939), ("needs_review", None, "", .752)])
        self.assertIn("The student is a suggestion (75% from the name read)", sheets[2]["reasons"][0])
        self.assertEqual(setting(.7).json, {"auto_identify": .7})
        lowered = {item["page"]: item for item in self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]["sheets"]}
        self.assertEqual((lowered[2]["status"], lowered[2]["identified_by"], lowered[2]["student"]), ("clean", "confidence", "Nguyen, Carol"))
        self.assertEqual(setting(.85).status_code, 200)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([row[0] for row in db.execute("SELECT auto_identify FROM bubble_settings ORDER BY id")], [.85, .7, .85])  # Every change is kept.
        # Identified all the same, and its confident double mark is recorded as handed in, "(AB)": a matter for grading, not for review.
        self.assertEqual((sheets[3]["status"], sheets[3]["identified_by"], sheets[3]["student"], sheets[3]["answers"]), ("clean", "confidence", "Example, Bob", "B(AB)DEABCDEA"))
        self.assertEqual(self.client.get(f"/api/bubble-sheets/sheets/{sheets[3]['id']}").json["current"]["flags"], {"identity": False, "version": False, "questions": [], "unread": False})
        # No digits read at all, the name exact and nobody else close: 90% of the name similarity alone.
        self.assertEqual((sheets[4]["status"], sheets[4]["identified_by"], sheets[4]["student"]), ("clean", "confidence", "O'Brien, Dylan James"))
        self.assertEqual((run["identity"], run["counts"]), ({"uin": 0, "confidence": 3, "review": 0, "none": 1}, {"clean": 3, "needs_review": 1, "reviewed": 0}))
        rows = list(csv.reader(io.StringIO(self.client.get(f"/api/bubble-sheets/runs/{run['id']}/export.csv").data.decode("utf-8-sig"))))
        self.assertEqual([row[5] for row in rows[1:]], ["digits and name read, 94%", "", "digits and name read, 94%", "name read, 90%"])
        # Two sheets identified as one student are never both believed: each goes back to the instructor.
        twice = self.read(self.scan([sheet(KEY, "A"), sheet(KEY, "B")]), [("123456189", "Alice Example"), ("123456799", "Alice Example")]).json["run"]
        self.assertEqual([(item["status"], item["identified_by"]) for item in twice["sheets"]], [("needs_review", "confidence")] * 2)
        self.assertTrue(all("Another sheet in this scan is recorded for the same student." in item["reasons"] for item in twice["sheets"]))
        self.assertEqual(self.client.post(f"/api/bubble-sheets/runs/{run['id']}/confirm-suggestions", json={"minimum": .85}, headers=HEADERS).status_code, 404)  # Replaced by the rule above.

    def test_a_confident_blank_is_an_answer_and_needs_no_review(self):
        message = self.scan([sheet({**KEY, 5: "", 9: ""}, "A"), sheet({**KEY, 5: "", 6: ""}, "B", faint={6: "E"})])
        run = self.read(message, [("123456789", "Alice Example"), ("223456780", "Bob Example")]).json["run"]
        first, second = run["sheets"]
        self.assertEqual((first["status"], first["reasons"], first["answers"]), ("clean", [], "BCDE·BCD·A"))
        # The faint mark is what the second sheet waits for; its blank is not opened beside it.
        self.assertEqual((second["status"], second["answers"]), ("needs_review", "BCDE··CDEA"))  # Question 6 counts for nothing until it is reviewed.
        self.assertEqual(self.client.get(f"/api/bubble-sheets/sheets/{second['id']}").json["current"]["flags"]["questions"], [6])
        self.assertFalse(any("blank" in reason for reason in second["reasons"]))
        # A reading retained from before this rule still says "is blank": it is no longer a reason to look at the sheet.
        with connect(self.app.config["DATABASE"]) as db:
            sheets = self.bubbles._sheets(db, run["id"])
        partly = "This page only partly resembles the bubble sheet (match 0.29)."
        alone = {"threshold": .7, "yields": False, "duplicate": None, "taken": set()}
        older = {**sheets[0], "reading": {**sheets[0]["reading"], "review": ["Question 5 is blank.", partly, "Question 9 is blank.", "Marks beyond question 10: questions 12, 13."]}}
        current = self.bubbles._current(older, None, alone)
        self.assertEqual((current["status"], current["reasons"], current["flags"]["questions"]), ("clean", [], []))
        # The retained resemblance doubt stands only where the rings under the marks were faint too.
        faint = {**older, "reading": {**older["reading"], "alignment": {**older["reading"]["alignment"], "ring": .05}}}
        self.assertEqual(self.bubbles._current(faint, None, alone)["reasons"], [partly])
        # A retained reading keeps its mark strengths, so today's rule decides it: a light but deliberate 0.16 (once "faint") is an answer.
        with connect(self.app.config["DATABASE"]) as db:
            stored = json.loads(db.execute("SELECT reading_json FROM bubble_sheets WHERE id=?", (first["id"],)).fetchone()[0])
        stored["answers"][0].update(state="unsure", answer="", unsure="B", marks={"A": 0, "B": .16, "C": 0, "D": 0, "E": 0})
        stored["review"].append("Question 1: a faint or erased mark at B.")
        self.assertEqual(reader.decide([stored["answers"][0]["marks"][letter] for letter in "ABCDE"]), ("single", "B", ""))

    def test_a_student_already_matched_is_not_offered_again_and_a_confident_match_yields_to_a_firm_one(self):
        self.client.post("/api/bubble-sheets/settings", json={"auto_identify": .7}, headers=HEADERS)
        # Page 1 is Alice by exact UIN. Page 2 looks like Alice at 94% (a digit off): it yields, stays unidentified, and Alice is not
        # offered for it. Page 3 has nobody.
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B"), sheet(KEY, "A")])
        run = self.read(message, [("123456789", "Alice Example"), ("123456189", "Alice Example"), ("", "")]).json["run"]
        pages = {item["page"]: item for item in run["sheets"]}
        self.assertEqual([(pages[page]["status"], pages[page]["identified_by"], pages[page]["student"]) for page in (1, 2)],
                         [("clean", "uin", "Example, Alice Marie"), ("needs_review", None, "")])
        self.assertEqual(pages[2]["reasons"], ["The closest student on the roster is already matched to another sheet of this assignment."])
        detail = self.client.get(f"/api/bubble-sheets/sheets/{pages[2]['id']}").json
        self.assertEqual(([student["name"] for student in detail["students"] if student["taken"]], detail["match"]["candidates"][0]["taken"], detail["current"]["flags"]["identity"]),
                         (["Example, Alice Marie"], True, True))
        # The same holds across scans of one assignment: Bob is recorded in this scan, so another scan's confident Bob yields too,
        # while an exact UIN there is an equal claim that only the instructor can settle.
        first = self.read(self.scan([sheet(KEY, "C")]), [("223456780", "Bob Example")]).json["run"]
        self.assertEqual([(item["status"], item["student"]) for item in first["sheets"]], [("clean", "Example, Bob")])
        second = self.read(self.scan([sheet(KEY, "D"), sheet(KEY, "E")]), [("223456180", "Bob Example"), ("223456780", "Bob Example")]).json["run"]
        self.assertEqual([(item["status"], item["identified_by"], item["student"]) for item in second["sheets"]],
                         [("needs_review", None, ""), ("needs_review", "uin", "Example, Bob")])
        self.assertIn("A sheet in another scan of this assignment is recorded for the same student.", second["sheets"][1]["reasons"])

    def test_the_latest_review_counts_here_and_in_other_scans_exactly_as_the_earlier_loader_read_it(self):
        first = self.read(self.scan([sheet(KEY, "A"), sheet(KEY, "B")]), [("123456789", "Alice Example"), ("223456780", "Bob Example")]).json["run"]
        students = {student["name"]: student for student in self.client.get(f"/api/bubble-sheets/sheets/{first['sheets'][0]['id']}").json["students"]}
        review = lambda sheet_id, name: self.assertEqual(self.client.post(f"/api/bubble-sheets/sheets/{sheet_id}/review", headers=HEADERS, json={
            "answers": list("BCDEABCDEA"), "version": "A", "section_id": students[name]["section_id"] if name else None,
            "enrollment_id": students[name]["enrollment_id"] if name else None}).status_code, 201)
        # Several reviews of one sheet: only the latest counts, a latest review naming nobody included.
        review(first["sheets"][0]["id"], "Nguyen, Carol")
        review(first["sheets"][0]["id"], None)
        review(first["sheets"][1]["id"], None)
        review(first["sheets"][1]["id"], "O'Brien, Dylan James")
        # Another scan of the assignment sees those sheets by their latest reviews: Carol (an earlier review) and Alice (the read)
        # are free, while Dylan is recorded, so a confident Dylan yields. (His sheet's marks differ from the reviewed one's: with the
        # same marks it would be that sheet scanned again, A SHEET SCANNED TWICE.)
        second = self.read(self.scan([sheet(KEY, "C"), sheet({**KEY, 1: "A"}, "D"), sheet(KEY, "E")]),
                           [("987650001", "Carol Nguyen"), ("555000191", "Dylan O'Brien"), ("123456789", "Alice Example")]).json["run"]
        self.assertEqual([(item["status"], item["identified_by"], item["student"]) for item in second["sheets"]],
                         [("clean", "uin", "Nguyen, Carol"), ("needs_review", None, ""), ("clean", "uin", "Example, Alice Marie")])
        detail = self.client.get(f"/api/bubble-sheets/sheets/{second['sheets'][0]['id']}").json
        self.assertEqual(sorted(student["name"] for student in detail["students"] if student["taken"]), ["Example, Alice Marie", "O'Brien, Dylan James"])

        def earlier_load(db, run_id):  # the loader before 2026-09-29: each sheet's latest review read on its own
            sheets = []
            for row in db.execute("""SELECT b.*,m.match_json current_match_json,sp.source_page_index,sp.raster_artifact_id,sp.page_artifact_id
                                     FROM bubble_sheets b JOIN scan_pages sp ON sp.id=b.scan_page_id LEFT JOIN bubble_matches m ON m.sheet_id=b.id AND m.matcher=?
                                     WHERE b.run_id=? ORDER BY sp.source_artifact_id,sp.source_page_index""", (matching.MATCHER, run_id)):
                item = dict(row)
                item["reading"], item["match"] = json.loads(item.pop("reading_json")), json.loads(item.pop("current_match_json") or item["match_json"])
                item.pop("match_json")
                for answer in item["reading"].get("answers", []):
                    if "marks" in answer:
                        answer["state"], answer["answer"], answer["unsure"] = reader.decide([answer["marks"][letter] for letter in "ABCDE"])
                item["review"] = db.execute("SELECT * FROM bubble_reviews WHERE sheet_id=? ORDER BY id DESC LIMIT 1", (item["id"],)).fetchone()
                sheets.append(item)
            return sheets

        def canonical(sheets):
            convert = lambda value: {"row": dict(value)} if isinstance(value, sqlite3.Row) else sorted(value) if isinstance(value, set) else repr(value)
            return json.dumps(sheets, sort_keys=True, default=convert)

        with connect(self.app.config["DATABASE"]) as db:
            threshold = self.bubbles.auto_identify(db)
            for run in (first["id"], second["id"]):
                # The same claims, in no particular order (`_sheets` keeps only the firmest claim per student).
                self.assertEqual(Counter(self.bubbles._claims(db, run, threshold)), Counter(self.bubbles._claim(item, threshold) for item in earlier_load(db, run)))
                now = canonical(self.bubbles._sheets(db, run))
                with patch.object(self.bubbles, "_load", earlier_load), \
                        patch.object(self.bubbles, "_claims", lambda db, run_id, threshold, repeats=frozenset(): [self.bubbles._claim(item, threshold) for item in earlier_load(db, run_id)]):
                    self.assertEqual(now, canonical(self.bubbles._sheets(db, run)))
            self.assertEqual([row[0] for row in db.execute("SELECT enrollment_id IS NULL FROM bubble_reviews ORDER BY id")], [0, 1, 1, 0])
            # One view's memo (PERFORMANCE, 2026-10-25: the Assignments list read Exam 2's booklets again for each of its 36 scans, about
            # 8 s): the same sheets, with the booklets, the repeats and each other run's claims read once for every scan of the view.
            runs, memo, calls = (first["id"], second["id"]) * 2, {}, Counter()

            def counted(name, real):
                def call(*args, **kwargs):
                    calls[name] += 1
                    return real(*args, **kwargs)
                return call
            with patch.object(self.bubbles, "booklet_versions", counted("booklets", self.bubbles.booklet_versions)), \
                    patch.object(self.bubbles, "_repeats", counted("repeats", self.bubbles._repeats)), \
                    patch.object(self.bubbles, "_claims", counted("claims", self.bubbles._claims)):
                shared = [canonical(self.bubbles._sheets(db, run, memo)) for run in runs]
            self.assertEqual(shared, [canonical(self.bubbles._sheets(db, run)) for run in runs])
            self.assertEqual(dict(calls), {"booklets": 1, "repeats": 1, "claims": 2})
        # Every view that goes through all the scans shares one memo: the Inbox list (polled while open) read the booklets once per
        # bubble-sheet scan too (2026-10-25, 2.3 s on live), and Home's Inbox count reads the same overviews.
        from datetime import datetime, timezone
        from workspace_overview import inbox_summary
        inbox, scans = self.app.extensions["inbox"], self.app.extensions["document_scans"]
        for name, view in (("Inbox list", lambda: inbox.listing()),
                           ("Home", lambda: self._with_db(lambda db: inbox_summary(db, inbox, scans, datetime.now(timezone.utc))))):
            calls.clear()
            with patch.object(self.bubbles, "booklet_versions", counted("booklets", lambda db, *exam: {})):
                view()
            # The Inbox list shows every scan, so it reads them; Home reads only the scans that decide its filing.
            self.assertEqual(calls["booklets"], 1, name) if name == "Inbox list" else self.assertLessEqual(calls["booklets"], 1, name)

    def _with_db(self, read):
        with self.app.extensions["rosters"].lock(), connect(self.app.config["DATABASE"]) as db:
            return read(db)

    def test_the_question_count_is_inferred_from_where_most_sheets_stop(self):
        # Most students answer to the end, a few stop early, a few leave a stray mark further down: the mode is the count when at
        # least 60% of the marked sheets share it (on the real sheets 97% to 100% did).
        self.assertEqual(reader.infer_question_count([17] * 157 + [16, 16, 19, 26, 27]), (17, .969))
        self.assertEqual(reader.infer_question_count([10, 10, 0, 10, 8]), (10, .75))  # A sheet without a single mark says nothing.
        for marks, message in (([10, 8, 6, 10, 9], "only 40% of them"), ([], "No sheet in this scan has a mark"), ([0, 0], "No sheet in this scan has a mark")):
            with self.assertRaisesRegex(reader.Undecided, message):
                reader.infer_question_count(marks)
        message = self.scan([sheet(KEY, "A"), sheet({**KEY, 10: ""}, "B"), sheet({**KEY, 14: "C"}, "A")] + [sheet(KEY, "B")] * 5)
        texts = [("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen"), ("555000111", "Dylan OBrien")] + [("", "")] * 4
        run = self.read(message, texts, question_count=None).json["run"]
        self.assertEqual((run["status"], run["question_count"], run["question_source"], run["summary"]["question_agreement"]), ("complete", 10, "inferred", .75))
        # Every sheet is kept to ten questions: the early stop is a blank tenth answer, the stray mark on 14 is nobody's answer.
        self.assertEqual([(item["status"], item["answers"]) for item in run["sheets"] if item["student"]],
                         [("clean", "BCDEABCDEA"), ("clean", "BCDEABCDE·"), ("clean", "BCDEABCDEA"), ("clean", "BCDEABCDEA")])
        self.assertEqual(len(self.client.get(f"/api/bubble-sheets/sheets/{run['sheets'][2]['id']}").json["reading"]["answers"]), 10)
        # An entered count is used as given, and says so.
        entered = self.read(message, texts, question_count=9).json["run"]
        self.assertEqual((entered["question_count"], entered["question_source"], entered["summary"]["question_agreement"]), (9, "entered", None))
        # Sheets that do not agree are not guessed at: the read fails with the reason, and the instructor enters the count.
        upto = lambda last: sheet({number: "A" for number in range(1, last + 1)}, "A")
        disagree = self.scan([upto(10)] * 4 + [upto(7)] * 3 + [upto(4)] * 3)  # Ten marked sheets stand on their own: 40% is no majority.
        failed = self.read(disagree, [("", "")] * 10, question_count=None).json["run"]
        self.assertEqual((failed["status"], failed["sheets"]), ("failed", []))
        self.assertIn("The sheets do not agree on a last question", failed["error"])
        self.assertIn("Enter the number of questions and read again.", failed["error"])
        # A few stragglers scanned on their own are a thin basis: the other scans of the same assignment vote too.
        # On its own this sheet, which stops at question 8, would say "eight questions".
        straggler = self.scan([sheet({**KEY, 10: "", 9: ""}, "A")])
        alone = self.read(straggler, [("", "")], question_count=None).json["run"]
        self.assertEqual((alone["status"], alone["question_count"], alone["summary"]["question_agreement"]), ("complete", 10, .667))
        for bad in (0, 101, "ten", 2.5):
            self.assertEqual(self.read(message, [], question_count=bad).status_code, 400, bad)

    def test_question_count_ignores_deleted_scans_and_restores_their_evidence(self):
        # INBOX / BUBBLE-SHEETS: a deleted five-question upload must not truncate a new ten-question reading.
        old = self.scan([sheet({number: "A" for number in range(1, 6)})] * 3)
        self.assertEqual(self.read(old, [("", "")] * 3, question_count=5).json["run"]["status"], "complete")
        job = self.inbox.get_by_message(old)
        self.inbox.remove(job["id"], job["version"], "delete")
        fresh = self.scan([sheet(KEY)])
        run = self.read(fresh, [("123456789", "Alice Example")], question_count=None).json["run"]
        self.assertEqual((run["status"], run["question_count"], run["summary"]["question_agreement"]), ("complete", 10, 1.0))
        detail = self.client.get(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}").json
        self.assertEqual("".join(answer["answer"] for answer in detail["reading"]["answers"]), "BCDEABCDEA")
        # Restoring the source restores its vote; the earlier ten-answer reading remains immutable.
        self.inbox.restore(job["id"])
        rerun = self.read(fresh, [("123456789", "Alice Example")], question_count=None).json["run"]
        self.assertEqual((rerun["question_count"], rerun["summary"]["question_agreement"]), (5, .75))
        self.assertEqual(len(self.client.get(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}").json["reading"]["answers"]), 10)

    def test_question_count_ignores_other_terms_of_the_same_assignment(self):
        # HIERARCHY / BUBBLE-SHEETS: the course-wide assignment is shared, but an earlier term's count is not.
        old = self.scan([sheet({number: "A" for number in range(1, 6)})] * 3)
        self.assertEqual(self.read(old, [("", "")] * 3, question_count=5).json["run"]["status"], "complete")
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Spring", "year": 2027,
                                                       "sections": [503], "is_current": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        section = next(item["id"] for item in self.client.get("/api/seating/catalog").json["sections"] if item["number"] == 503)
        fresh = self.scan([sheet(KEY)])
        run = self.read(fresh, [("", "")], section_ids=[section], question_count=None).json["run"]
        self.assertEqual((run["status"], run["question_count"], run["summary"]["question_agreement"]), ("complete", 10, 1.0))
        self.assertEqual(len(self.client.get(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}").json["reading"]["answers"]), 10)

    def test_no_sections_are_chosen_each_sheet_takes_its_section_from_its_student(self):
        # A second section with its own student: nothing says beforehand which section a sheet is from.
        state = self.client.get("/api/state").json
        rows = [["", "Zamora, Dana", "444000123", "Registered", "", "", "", "", "", "", "", "dana@example.test"]]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/502/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B")])
        queued = self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": self.assignment["id"]}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202, queued.json)
        ocr = lambda content, boxes: {"reads": [{"text": text} for pair in (("123456789", "Alice Example"), ("444000123", "Dana Zamora")) for text in pair][:len(boxes)], "seconds": 0}
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
            self.assertTrue(self.bubbles.process_one())
        run = self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]
        # Matched against every section of the course's current term; each student brings the section along.
        self.assertEqual((run["sections"], [(item["status"], item["student"]) for item in run["sheets"]]), ([501, 502], [("clean", "Example, Alice Marie"), ("clean", "Zamora, Dana")]))
        rows = list(csv.reader(io.StringIO(self.client.get(f"/api/bubble-sheets/runs/{run['id']}/export.csv").data.decode("utf-8-sig"))))
        self.assertEqual([row[4] for row in rows[1:]], ["501", "502"])
        self.assertEqual(self.scans.review_item(message)["overview"]["headline"], "2 bubble sheets · Test 1 · Sections 501 and 502")
        self.assertEqual(self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": "missing"}, headers=HEADERS).status_code, 400)

    def test_reading_a_scan_again_keeps_the_reviews_already_made(self):
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B")])
        run = self.read(message, [("", ""), ("223456780", "Bob Example")]).json["run"]
        students = self.client.get(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}").json["students"]
        self.assertEqual(self.client.post(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}/review", headers=HEADERS, json={
            "answers": list("BCDEABCDEE"), "version": "A", "note": "Wrote no name", "section_id": students[2]["section_id"], "enrollment_id": students[2]["enrollment_id"]}).status_code, 201)
        again = self.read(message, [("", ""), ("223456780", "Bob Example")]).json["run"]
        self.assertNotEqual(again["id"], run["id"])
        self.assertEqual([(item["status"], item["identified_by"], item["student"], item["answers"]) for item in again["sheets"]],
                         [("reviewed", "review", "Nguyen, Carol", "BCDEABCDEE"), ("clean", "uin", "Example, Bob", "BCDEABCDEA")])
        with connect(self.app.config["DATABASE"]) as db:
            carried = db.execute("SELECT actor,note FROM bubble_reviews ORDER BY id DESC LIMIT 1").fetchone()
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='bubble_sheets_read' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertTrue(carried["actor"].startswith("carried over from workspace's review of 20") and carried["note"] == "Wrote no name", dict(carried))
        self.assertEqual(event["reviews_carried_over"], 1)

    def test_a_changed_matching_rule_reaches_retained_reads_without_reading_again_or_touching_reviews(self):
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B")])
        nobody = {"state": "unmatched", "method": None, "enrollment_id": None, "section_id": None, "confidence": 0.0, "candidates": [], "notes": []}
        with patch.object(matching, "MATCHER", "an-earlier-rule"), patch.object(matching, "match", return_value=nobody):
            run = self.read(message, [("123456189", "Alice Example"), ("223456180", "Bob Example")]).json["run"]
        self.assertEqual([item["status"] for item in run["sheets"]], ["needs_review"] * 2)
        # The instructor settles one sheet by hand before the rule changes.
        students = self.client.get(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}").json["students"]
        self.assertEqual(self.client.post(f"/api/bubble-sheets/sheets/{run['sheets'][0]['id']}/review", headers=HEADERS, json={
            "answers": list("BCDEABCDEA"), "version": "A", "section_id": students[3]["section_id"], "enrollment_id": students[3]["enrollment_id"]}).status_code, 201)
        # The worker matches the retained text again under the current rule: nothing is read again (OCR is not even available here).
        with patch("faculty_ocr.recognize_fields", side_effect=AssertionError("no page is read again")):
            self.assertTrue(self.bubbles.process_one())
        after = self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]
        self.assertEqual([(item["status"], item["identified_by"], item["student"]) for item in after["sheets"]],
                         [("reviewed", "review", "O'Brien, Dylan James"), ("clean", "confidence", "Example, Bob")])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT matcher,COUNT(*) FROM bubble_matches GROUP BY matcher ORDER BY matcher").fetchall()[0][1], 2)
            self.assertEqual({row[0]: row[1] for row in db.execute("SELECT matcher,COUNT(*) FROM bubble_matches GROUP BY matcher")}, {"an-earlier-rule": 2, matching.MATCHER: 2})
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='bubble_sheets_rematched'").fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bubble_reviews").fetchone()[0], 1)
            with self.assertRaises(Exception):
                db.execute("UPDATE bubble_matches SET match_json='{}'")
        self.assertEqual((event["matcher"], event["sheets"], event["pages_read_again"], event["reviews_changed"]), (matching.MATCHER, 2, 0, 0))
        self.assertFalse(self.bubbles.process_one())

    def test_a_sheet_scanned_twice_is_one_sheet(self):
        # Requirement, 2026-10-24: a student's paper went through the scanner in two stacks. The same Scantron label with
        # the same marks is one sheet: scored once, from the first scan, and nobody's duplicate; different marks are still asked.
        from document_identity.media import render_barcode, render_pages
        documents = self.app.extensions["documents"]
        with connect(self.app.config["DATABASE"]) as db:
            enrolled = {row[1]: row[0] for row in db.execute("SELECT id,uin FROM student_enrollments")}
        version = documents.register_stamp_pages(self.assignment["id"], 1)
        batch = documents.create_batch(version["id"], self.sections[501], 2, str(uuid.uuid4()),
                                       students=[enrolled["987650001"], enrolled["123456789"]], sheet_labels=True)
        self.assertTrue(documents.build_one())
        with connect(self.app.config["DATABASE"]) as db:
            carol, alice = [entry["token"] for entry in documents.label_manifest(db, batch["id"]) if entry.get("sheet")]

        def labeled(image, token):
            image.paste(next(render_pages(render_barcode(token), 200)).convert("L"), (760, 40))
            return image
        nobody = [("", ""), ("", "")]
        first = self.scan([labeled(sheet(KEY, "A"), carol), labeled(sheet(KEY, "B"), alice)])
        self.read(first, nobody)
        # Carol's version bubble reads blank this time (a faint mark, Exam 2, 2026-10-25): her answers alone say it is the same sheet.
        again = self.scan([labeled(sheet(KEY, "", shift=(4, 3)), carol), labeled(sheet({**KEY, 2: "A"}, "B"), alice)])
        run = self.read(again, nobody).json["run"]
        pages = {item["page"]: item for item in run["sheets"]}
        self.assertEqual((pages[1]["status"], pages[1]["student"], pages[1]["reasons"]), ("repeat", "", []))
        self.assertEqual(pages[2]["status"], "needs_review")  # Alice's marks differ: not the same sheet
        before = {item["page"]: item for item in self.client.get(f"/api/bubble-sheets/messages/{first}").json["run"]["sheets"]}
        self.assertEqual((before[1]["status"], before[1]["student"]), ("clean", "Nguyen, Carol"))
        self.assertEqual(before[2]["status"], "needs_review")
        # Exactly the later scan's copy of Carol's sheet repeats the first scan's.
        with connect(self.app.config["DATABASE"]) as db:
            repeat = db.execute("SELECT b.id FROM bubble_sheets b JOIN bubble_runs r ON r.id=b.run_id JOIN scan_pages sp ON sp.id=b.scan_page_id WHERE r.message_id=? AND sp.source_page_index=1",
                                (again,)).fetchone()[0]
            self.assertEqual(self.bubbles._repeats(db, self.assignment["id"]), {repeat: {"message_id": first, "page": 1}})
        # Alice's second sheet reviewed as her, with the first sheet's answers: the instructor's review stands for that sticker, and the
        # first scan's copy repeats it (requirement, 2026-10-25).
        detail = self.client.get(f"/api/bubble-sheets/sheets/{pages[2]['id']}").json
        alice_id = next(student for student in detail["students"] if student["name"].startswith("Example, Alice"))
        saved = self.client.post(f"/api/bubble-sheets/sheets/{pages[2]['id']}/review", headers=HEADERS,
                                 json={"answers": [KEY[n] for n in range(1, 11)], "version": "B", "section_id": alice_id["section_id"], "enrollment_id": alice_id["enrollment_id"]})
        self.assertEqual((saved.status_code, saved.json["current"]["status"]), (201, "reviewed"))
        before = {item["page"]: item for item in self.client.get(f"/api/bubble-sheets/messages/{first}").json["run"]["sheets"]}
        self.assertEqual((before[2]["status"], before[2]["student"]), ("repeat", ""))
        # Carol's sheet scanned a third time with no label read (Eva Marsh's, Exam 2): reviewed as Carol, it is hers, and both
        # labeled copies repeat it.
        third = self.scan([sheet(KEY, "A", shift=(2, 2))])
        lone = self.read(third, [("", "")]).json["run"]["sheets"][0]
        self.assertEqual((lone["status"], lone["student"]), ("needs_review", ""))
        carol_id = next(student for student in detail["students"] if student["name"].startswith("Nguyen, Carol"))
        saved = self.client.post(f"/api/bubble-sheets/sheets/{lone['id']}/review", headers=HEADERS,
                                 json={"answers": [KEY[n] for n in range(1, 11)], "version": "A", "section_id": carol_id["section_id"], "enrollment_id": carol_id["enrollment_id"]})
        self.assertEqual((saved.status_code, saved.json["current"]["status"], saved.json["current"]["reasons"]), (201, "reviewed", []))
        before = {item["page"]: item for item in self.client.get(f"/api/bubble-sheets/messages/{first}").json["run"]["sheets"]}
        self.assertEqual([before[page]["status"] for page in (1, 2)], ["repeat", "repeat"])
        # Alice's sheet once more with no label read, named by the UIN written on it (Lyle Ensor's, Exam 2): another sheet holds her
        # by label and review and the marks agree, so it is that sheet again and asks nothing.
        fourth = self.scan([sheet({**KEY, 2: "A"}, "B", shift=(1, 3))])  # the marks of her reviewed copy, as read
        again_alice = self.read(fourth, [("123456789", "Alice Example")]).json["run"]["sheets"][0]
        self.assertEqual((again_alice["status"], again_alice["reasons"]), ("repeat", []))

    def test_a_scantron_label_names_the_student_and_leaves_the_sheet_an_unlabeled_page(self):
        # DOCUMENTS "SCANTRON LABELS" (requirement, 2026-10-15): the label printed for a student's bubble sheet identifies it.
        from document_identity.media import render_barcode, render_pages
        documents = self.app.extensions["documents"]
        with connect(self.app.config["DATABASE"]) as db:
            enrolled = {row[1]: row[0] for row in db.execute("SELECT id,uin FROM student_enrollments")}
        version = documents.register_stamp_pages(self.assignment["id"], 1)
        batch = documents.create_batch(version["id"], self.sections[501], 2, str(uuid.uuid4()),
                                       students=[enrolled["987650001"], enrolled["123456789"]], sheet_labels=True)
        self.assertTrue(documents.build_one())
        with connect(self.app.config["DATABASE"]) as db:
            carol, alice = [entry["token"] for entry in documents.label_manifest(db, batch["id"]) if entry.get("sheet")]

        def labeled(image, token):
            mark = next(render_pages(render_barcode(token), 200)).convert("L")
            image.paste(mark, (760, 40))
            return image
        message = self.scan([labeled(sheet(KEY, "A"), carol), labeled(sheet(KEY, "B"), alice), sheet(KEY, "A")])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([row[0] for row in db.execute("SELECT status FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (message,))], ["unreadable"] * 3)
        # Nothing legible was written on Carol's sheet; Alice's carries Bob's exact UIN, which argues against her label.
        run = self.read(message, [("", ""), ("223456780", "Bob Example"), ("555000111", "Dylan O'Brien")]).json["run"]
        pages = {item["page"]: item for item in run["sheets"]}
        self.assertEqual((pages[1]["match"], pages[1]["identified_by"], pages[1]["student"], pages[1]["status"]), ("matched", "label", "Nguyen, Carol", "clean"))
        self.assertEqual((pages[2]["match"], pages[2]["identified_by"], pages[2]["status"]), ("suggested", None, "needs_review"))
        self.assertIn("The Scantron label is Example, Alice Marie's, but the UIN read is Example, Bob's.", pages[2]["reasons"])
        self.assertEqual((pages[3]["identified_by"], pages[3]["student"]), ("uin", "O'Brien, Dylan James"))  # An unlabeled sheet, as before.
        self.assertEqual(run["identity"], {"uin": 1, "confidence": 0, "review": 0, "none": 1, "label": 1})
        rows = list(csv.reader(io.StringIO(self.client.get(f"/api/bubble-sheets/runs/{run['id']}/export.csv").data.decode("utf-8-sig"))))
        self.assertEqual(rows[1][:6], ["1", "clean", "Nguyen, Carol", "987650001", "501", "Scantron label"])
        # A label of another assignment names nobody.
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(self.bubbles._labels(db, message, "another-assignment"), {})

    def test_without_local_ocr_every_sheet_waits_for_a_manual_match(self):
        message = self.scan([sheet(KEY, "A")])
        self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": self.assignment["id"], "section_ids": [self.sections[501]], "question_count": 10}, headers=HEADERS)
        busy = self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": self.assignment["id"], "section_ids": [self.sections[501]], "question_count": 10}, headers=HEADERS)
        self.assertEqual(busy.status_code, 409)
        with patch("faculty_ocr.available", return_value=False):
            self.assertTrue(self.bubbles.process_one())
        only, = self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]["sheets"]
        self.assertEqual((only["match"], only["answers"], only["status"]), ("unmatched", "BCDEABCDEA", "needs_review"))
        self.assertIn("Local OCR is not installed", " ".join(only["reasons"]))

    def test_a_later_ocr_batch_failing_blames_only_the_sheets_it_never_read(self):
        # Requirement, 2026-11-06: a sheet whose handwriting WAS read keeps its suggestion and carries no "OCR could not read" note;
        # only the sheets no successful OCR call covered are held for a manual match, as when OCR is missing.
        import faculty_ocr
        from bubble_sheets import service
        message = self.scan([sheet(KEY, "A"), sheet(KEY, "B")])
        queued = self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": self.assignment["id"], "section_ids": [self.sections[501]],
                                                                                   "question_count": 10}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202)
        calls = []

        def ocr(content, boxes):
            calls.append(len(boxes))
            if len(calls) == 2:
                raise faculty_ocr.OcrError("Another local OCR task is running; try again after it completes.")
            return {"reads": [{"text": "12345678"}, {"text": "Alice Example"}][:len(boxes)], "seconds": 0}  # a UIN one digit short: suggested
        with patch.object(service, "OCR_BATCH", 1), patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
            self.assertTrue(self.bubbles.process_one())
        self.assertEqual(len(calls), 2)
        first, second = sorted(self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]["sheets"], key=lambda item: item["page"])
        self.assertEqual((first["status"], first["identified_by"], first["student"]), ("clean", "confidence", "Example, Alice Marie"))
        self.assertNotIn("Local OCR could not read", " ".join(first["reasons"]))
        self.assertEqual((second["match"], second["status"]), ("unmatched", "needs_review"))
        self.assertIn("Local OCR could not read", " ".join(second["reasons"]))


if __name__ == "__main__":
    unittest.main()
