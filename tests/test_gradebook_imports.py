"""Gradebook score imports: file formats, exact UIN matching and the import plan. Synthetic data only."""
import csv
import io
import json
import unittest

from openpyxl import Workbook

from gradebook import common, imports
from roster_import import MAX_COLUMNS, parse_roster, read_table

ALICE, BOB, CARA, DEV = "001234567", "123456789", "234567890", "345678901"
STUDENTS = [
    {"row": "s507:1000000001", "section_id": "s507", "section": 507, "name": "Example, Alice", "uin": ALICE},
    {"row": "s507:1000000002", "section_id": "s507", "section": 507, "name": "Example, Bob", "uin": BOB},
    {"row": "s508:1000000003", "section_id": "s508", "section": 508, "name": "Example, Cara", "uin": CARA},
    # One UIN in two sections of the term: never guessed.
    {"row": "s507:1000000004", "section_id": "s507", "section": 507, "name": "Example, Dev", "uin": DEV},
    {"row": "s508:1000000004", "section_id": "s508", "section": 508, "name": "Example, Dev", "uin": DEV},
]
ROW = {student["uin"]: student["row"] for student in STUDENTS[:3]}
SETUP = {"categories": [{"id": "cat_exams", "name": "Exams"}],
         "items": [{"id": "item_q1", "title": "Quiz 1", "points": "10", "archived": False, "sections": []},
                   {"id": "item_old", "title": "Old Quiz", "points": "5", "archived": True, "sections": []},
                   {"id": "item_lab", "title": "Lab", "points": "5", "archived": False, "sections": ["s508"]}]}
COURSE_HEAD = ["First Name", "Last Name", "SID", "Email", "Sections"]


def csv_bytes(rows, encoding="utf-8-sig"):
    text = io.StringIO(newline="")
    csv.writer(text).writerows(rows)
    return text.getvalue().encode(encoding)


def course_rows(assignments, students):
    """A Gradescope course "Download Grades" export: four columns per assignment."""
    head = [*COURSE_HEAD]
    for title, _ in assignments:
        head += [title, f"{title} - Max Points", f"{title} - Submission Time", f"{title} - Lateness (H:M:S)"]
    body = []
    for first, last, sid, scores in students:
        row = [first, last, sid, f"{first.lower()}@example.test", "507"]
        for (_, maximum), score in zip(assignments, scores):
            row += [score, maximum, "2026-09-01 10:00:00 -0500" if score else "", "00:00:00"]
        body.append(row)
    return [head, *body]


def xlsx_bytes(rows, numeric_sid=None):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    if numeric_sid:
        # Excel keeps a UIN as a number with a leading-zero display format.
        cell, value = numeric_sid
        workbook.active[cell] = value
        workbook.active[cell].number_format = "000000000"
    target = io.BytesIO()
    workbook.save(target)
    workbook.close()
    return target.getvalue()


def plan(parsed, options=None, cells=None, setup=SETUP, revision=4, students=STUDENTS):
    return imports.plan(parsed, options=options, students=students, setup=setup, revision=revision, cells=cells or {})


def cell(identifier, status="scored", points="8", source="import"):
    return {"id": identifier, "status": status, "points": points if status == "scored" else None, "source": source,
            "note": "", "actor": "workspace", "at": "2026-09-20T10:00:00Z"}


class ParseTests(unittest.TestCase):
    def test_gradescope_course_csv(self):
        rows = course_rows([("Quiz 1", "10"), ("Homework 2", "20")],
                           [("Alice", "Example", ALICE, ["8.333333333", ""]), ("Bob", "Example", BOB, ["EX", "19.5"]),
                            ("Cara", "Example", CARA, ["mi", "abc"])])
        rows[3][6] = "12"  # Cara's Quiz 1 maximum disagrees with the others.
        parsed = imports.parse(csv_bytes(rows), "course_grades.csv")
        self.assertEqual(parsed["format"], "gradescope_course")
        self.assertEqual(parsed["sha256"], common.digest(csv_bytes(rows)))
        self.assertEqual([(c["title"], c["max_points"]) for c in parsed["columns"]], [("Quiz 1", "10"), ("Homework 2", "20")])
        alice, bob, cara = parsed["rows"]
        self.assertEqual((alice["line"], alice["sid"], alice["name"]), (2, ALICE, "Alice Example"))
        quiz, homework = (column["key"] for column in parsed["columns"])
        self.assertEqual(alice["values"][quiz], {"kind": "scored", "points": "8.3333", "raw": "8.333333333"})
        self.assertEqual(alice["values"][homework]["kind"], "blank")
        self.assertEqual(bob["values"][quiz]["kind"], "excused")
        self.assertEqual(bob["values"][homework]["points"], "19.5")
        self.assertEqual(cara["values"][quiz]["kind"], "missing")
        self.assertEqual(cara["values"][homework], {"kind": "unreadable", "points": None, "raw": "abc"})
        self.assertTrue(any("rounded half up" in warning and warning.startswith("1 score") for warning in parsed["warnings"]))
        self.assertTrue(any("different maximum points" in warning for warning in parsed["warnings"]))

    def test_gradescope_course_xlsx_keeps_leading_zero_and_wide_files(self):
        assignments = [(f"Quiz {number}", "10") for number in range(1, 21)]  # 85 columns: more than a roster may have.
        rows = course_rows(assignments, [("Alice", "Example", "placeholder", ["7"] * 20), ("Bob", "Example", BOB, ["9"] * 20)])
        self.assertGreater(len(rows[0]), MAX_COLUMNS)
        parsed = imports.parse(xlsx_bytes(rows, numeric_sid=("C2", 1234567)), "grades.XLSX")
        self.assertEqual(parsed["format"], "gradescope_course")
        self.assertEqual(len(parsed["columns"]), 20)
        self.assertEqual([row["sid"] for row in parsed["rows"]], [ALICE, BOB])
        self.assertEqual(parsed["rows"][0]["values"][parsed["columns"][-1]["key"]]["points"], "7")
        with self.assertRaisesRegex(ValueError, "too many columns"):
            read_table(csv_bytes(rows), "grades.csv")  # The roster's bound stays the default.
        self.assertEqual(len(read_table(csv_bytes(rows), "grades.csv", max_columns=1024)[0]), len(rows[0]))

    def test_gradescope_assignment_export(self):
        rows = [["Name", "SID", "Email", "Total Score", "Max Points", "Status", "Submission ID", "1: Problem (5.0 pts)"],
                ["Alice Example", ALICE, "a@example.test", "4.5", "10.0", "Graded", "1", "4.5"],
                ["Bob Example", BOB, "b@example.test", "", "10.0", "Missing", "", ""],
                ["Cara Example", CARA, "c@example.test", "3.0", "10.0", "Ungraded", "3", ""]]
        parsed = imports.parse(csv_bytes(rows), "Quiz_3_scores.csv")
        self.assertEqual(parsed["format"], "gradescope_assignment")
        self.assertEqual(parsed["columns"], [{"key": "c3", "title": "Quiz 3", "max_points": "10"}])
        alice, bob, cara = (row["values"]["c3"] for row in parsed["rows"])
        self.assertEqual(alice["points"], "4.5")
        self.assertEqual(bob["kind"], "blank")  # A Missing status is a blank: only the instructor's option makes it missing.
        self.assertEqual((cara["kind"], cara["raw"]), ("unreadable", "Ungraded (3.0)"))
        self.assertTrue(any("not graded yet" in warning for warning in parsed["warnings"]))
        self.assertEqual(imports.parse(csv_bytes(rows), "Homework 4 scores.xlsx.csv")["columns"][0]["title"], "Homework 4 scores.xlsx")
        self.assertEqual(imports.parse(csv_bytes(rows), "Lab 2 Scores.csv")["columns"][0]["title"], "Lab 2")

    def test_generic_sheet_with_points_possible_and_encodings(self):
        rows = [["Student", "SIS User ID", "ID", "Section", "Quiz 1", "Notes", "Current Score", "Bonus"],
                ["    Points Possible", "", "", "", "10", "", "(read only)", ""],
                ["", "", "", "", "Manual Posting", "", "", ""],  # Canvas's posting policy: neither an ID nor a name.
                ["Muñoz, Zoë", ALICE, "55", "507", "ex", "late", "90", " 2 "],
                ["Example, Bob", BOB, "56", "508", "MISSING", "", "80", ""]]
        for encoding in ("utf-8-sig", "cp1252"):
            with self.subTest(encoding=encoding):
                parsed = imports.parse(csv_bytes(rows, encoding), "canvas.csv")
                self.assertEqual(parsed["format"], "generic")
                self.assertEqual([(c["title"], c["max_points"]) for c in parsed["columns"]], [("Quiz 1", "10"), ("Bonus", None)])
                self.assertEqual([row["sid"] for row in parsed["rows"]], [ALICE, BOB])
                self.assertEqual(parsed["rows"][0]["name"], "Muñoz, Zoë")
                quiz, bonus = (column["key"] for column in parsed["columns"])
                self.assertEqual([row["values"][quiz]["kind"] for row in parsed["rows"]], ["excused", "missing"])
                self.assertEqual(parsed["rows"][0]["values"][bonus]["points"], "2")
                warnings = " ".join(parsed["warnings"])
                self.assertIn("“Notes”", warnings)
                self.assertIn("“Current Score”", warnings)
                self.assertNotIn("Section", warnings)  # Identity columns are never score columns, and not worth a warning.
                self.assertIn("1 row with neither an ID nor a name was not read", warnings)

    def test_values(self):
        tally = {"rounded": 0}
        for raw, kind, points in [("12", "scored", "12"), (" 12.50 ", "scored", "12.5"), ("0", "scored", "0"), (".5", "scored", "0.5"),
                                  ("7.", "scored", "7"), ("100000", "scored", "100000"), ("100000.00001", "scored", "100000"),
                                  ("100001", "unreadable", None), ("-3", "unreadable", None), ("NaN", "unreadable", None),
                                  ("1,000", "unreadable", None), ("1e3", "unreadable", None), ("+4", "unreadable", None),
                                  ("85%", "unreadable", None), ("Excused", "excused", None), ("EX", "excused", None),
                                  ("mi", "missing", None), ("", "blank", None), ("  ", "blank", None)]:
            with self.subTest(raw=raw):
                value = imports._value(raw, tally)
                self.assertEqual((value["kind"], value["points"]), (kind, points))
        self.assertEqual(tally["rounded"], 1)
        self.assertEqual(len(imports._value("x" * 500, tally)["raw"]), 40)
        self.assertEqual(imports._value("9" * 9000, tally)["kind"], "unreadable")

    def test_refusals(self):
        cases = [(csv_bytes([["Name", "Email"], ["A", "a@example.test"]]), "a.csv", "not a score export"),
                 (csv_bytes([["UIN", "Quiz 1"]]), "a.csv", "no student rows"),
                 (csv_bytes([["UIN", "Notes"], [ALICE, "late"]]), "a.csv", "nothing to import"),
                 (csv_bytes([["UIN", "Quiz"], [ALICE, "1", "2"]]), "a.csv", "more cells"),
                 (csv_bytes([["SID", "Total Score", "Max Points", "SID"], [ALICE, "1", "2", ALICE]]), "a.csv", "more than one"),
                 (csv_bytes([[], [""]]), "a.csv", "no column headings"),
                 (b"", "a.csv", "score sheet file"), (b"x", "a.pdf", "score sheet export")]
        for content, name, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                imports.parse(content, name)
        workbook = Workbook()
        workbook.active.append(["UIN", "Quiz 1"])
        workbook.active.append([ALICE, "=1+1"])
        target = io.BytesIO()
        workbook.save(target)
        with self.assertRaisesRegex(ValueError, "The score sheet contains a formula"):
            imports.parse(target.getvalue(), "a.xlsx")

    def test_roster_parser_unchanged(self):
        # The roster keeps its own reader bounds and messages after sharing `read_table`'s reading.
        rows = [["Name", "UIN"], ["Example, Alice", ALICE]]
        self.assertEqual(parse_roster(csv_bytes(rows), "r.csv")["students"][0]["uin"], ALICE)
        with self.assertRaisesRegex(ValueError, "^Choose a nonempty roster file of 10 MB or less.$"):
            parse_roster(b"", "r.csv")
        with self.assertRaisesRegex(ValueError, "^Row 1 has too many columns or an invalid cell.$"):
            parse_roster(csv_bytes([[f"h{i}" for i in range(65)]]), "r.csv")


class PlanTests(unittest.TestCase):
    def parsed(self, quiz=("8", "", "7", "9"), lab=("4", "3", "2", "1"), extra=()):
        # Two spaces in "Quiz  1" still match the gradebook's "Quiz 1"; Reading gives no maximum anywhere.
        rows = course_rows([("Quiz  1", "10"), ("Lab", "5"), ("Homework 2", "20"), ("Reading", "")],
                           [("Alice", "Example", ALICE, [quiz[0], lab[0], "15", "1"]),
                            ("Bob", "Example", BOB, [quiz[1], lab[1], "16", "2"]),
                            ("Cara", "Example", CARA, [quiz[2], lab[2], "", "3"]),
                            ("Dev", "Example", DEV, [quiz[3], lab[3], "18", "4"]), *extra])
        return imports.parse(csv_bytes(rows), "course.csv")

    def keys(self, parsed):
        return {column["title"]: column["key"] for column in parsed["columns"]}

    def test_suggested_mapping_and_new_column_without_points(self):
        parsed = self.parsed()
        result = plan(parsed)
        keys = self.keys(parsed)
        mapping = {column["title"]: column["mapping"] for column in result["columns"]}
        self.assertEqual(mapping["Quiz  1"], {"action": "item", "item_id": "item_q1"})  # Title matched ignoring case and spacing.
        self.assertEqual(mapping["Lab"], {"action": "item", "item_id": "item_lab"})
        self.assertEqual(mapping["Homework 2"], {"action": "new", "title": "Homework 2", "category_id": None, "points": "20"})
        self.assertEqual(mapping["Reading"], {"action": "new", "title": "Reading", "category_id": None, "points": None})
        self.assertFalse(result["ready"])
        self.assertTrue(any("Give the new column “Reading” its points" in warning for warning in result["warnings"]))
        self.assertNotIn(keys["Reading"], {change["column"] for change in result["changes"]})
        self.assertEqual(result["new_items"], [{"column": keys["Homework 2"], "title": "Homework 2", "category_id": None, "points": "20"},
                                               {"column": keys["Reading"], "title": "Reading", "category_id": None, "points": None}])
        homework = [change for change in result["changes"] if change["column"] == keys["Homework 2"]]
        self.assertEqual([(change["name"], change["action"], change["item_id"]) for change in homework],
                         [("Example, Alice", "add", None), ("Example, Bob", "add", None)])
        # Lab applies to section 508 only: Alice and Bob (507) are not applicable, Cara is.
        lab = result["columns"][1]["counts"]
        self.assertEqual((lab["not_applicable"], lab["add"]), (2, 1))
        # With points given by the instructor, the plan is ready.
        options = {"mapping": {**{column["key"]: column["mapping"] for column in result["columns"]},
                               keys["Reading"]: {"action": "new", "title": "Reading", "category_id": "cat_exams", "points": "5"}}}
        ready = plan(parsed, options)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["columns"][3]["counts"]["add"], 3)

    def test_archived_and_duplicate_titles_are_not_suggested(self):
        rows = course_rows([("Old Quiz", "5"), ("Quiz 1", "10"), ("quiz 1", "10"), ("Unscored", "4")],
                           [("Alice", "Example", ALICE, ["1", "2", "3", ""])])
        result = plan(imports.parse(csv_bytes(rows), "course.csv"))
        self.assertEqual([column["mapping"]["action"] for column in result["columns"]], ["skip", "item", "skip", "skip"])
        warnings = " ".join(result["warnings"])
        self.assertIn("archived", warnings)
        self.assertIn("two columns titled “quiz 1”", warnings)

    def test_change_actions(self):
        parsed = self.parsed()
        quiz = self.keys(parsed)["Quiz  1"]
        cells = {"item_q1": {ROW[ALICE]: cell(11, points="8.0"),  # same value from an earlier import
                             ROW[CARA]: cell(12, points="6", source="instructor"),  # typed by hand
                             ROW[BOB]: cell(13, status="cleared", source="instructor")}}
        result = plan(parsed, {"mapping": None}, cells)
        actions = {change["name"]: change["action"] for change in result["changes"] if change["column"] == quiz}
        self.assertEqual(actions, {"Example, Alice": "same", "Example, Cara": "kept"})  # Bob's blank changes nothing.
        counts = result["columns"][0]["counts"]
        self.assertEqual((counts["blank"], counts["same"], counts["kept"]), (1, 1, 1))
        cara = next(change for change in result["changes"] if change["column"] == quiz and change["name"] == "Example, Cara")
        self.assertEqual((cara["current"]["id"], cara["proposed"], cara["section"]), (12, {"status": "scored", "points": "7"}, 508))
        replaced = plan(parsed, {"replace": [ROW[CARA]], "blank_as_missing": True}, cells)
        actions = {change["name"]: (change["action"], change["proposed"]["status"]) for change in replaced["changes"] if change["column"] == quiz}
        # Bob's cell was cleared by hand: kept until Replace is ticked for him too.
        self.assertEqual(actions, {"Example, Alice": ("same", "scored"), "Example, Bob": ("kept", "missing"),
                                   "Example, Cara": ("update", "scored")})
        both = plan(parsed, {"replace": [ROW[BOB], ROW[CARA]], "blank_as_missing": True}, cells)
        self.assertEqual(next(change["action"] for change in both["changes"] if change["column"] == quiz and change["name"] == "Example, Bob"), "add")
        imported = {"item_q1": {ROW[CARA]: cell(14, points="6")}}  # An earlier import is replaced without asking.
        self.assertEqual(next(change["action"] for change in plan(parsed, None, imported)["changes"]
                              if change["column"] == quiz and change["name"] == "Example, Cara"), "update")
        self.assertEqual(replaced["counts"]["add"], sum(column["counts"]["add"] for column in replaced["columns"]))

    def test_unmatched_rows_are_listed_never_guessed(self):
        extra = [("Eve", "Example", "12345678", ["5", "1", "1", "1"]),  # eight digits: never padded
                 ("Fay", "Example", "999999999", ["5", "1", "1", "1"]),  # not on the roster
                 ("Gus", "Example", "", ["5", "1", "1", "1"]),
                 ("Bob", "Again", BOB, ["6", "1", "1", "1"])]  # Bob twice: neither row is used
        parsed = self.parsed(extra=extra)
        result = plan(parsed)
        reasons = {entry["name"]: (entry["line"], entry["reason"]) for entry in result["unmatched"]}
        self.assertIn("not a nine-digit UIN", reasons["Eve Example"][1])
        self.assertIn("No student", reasons["Fay Example"][1])
        self.assertIn("no UIN", reasons["Gus Example"][1])
        self.assertIn("lines 3, 9", reasons["Bob Example"][1])
        self.assertIn("lines 3, 9", reasons["Bob Again"][1])
        self.assertIn("more than one section of this term (507, 508)", reasons["Dev Example"][1])
        self.assertEqual(result["counts"]["unmatched"], 6)
        self.assertEqual({change["name"] for change in result["changes"]}, {"Example, Alice", "Example, Cara"})
        text = json.dumps(result)
        for secret in (ALICE, BOB, CARA, DEV, "12345678", "999999999"):
            self.assertNotIn(secret, text)

    def test_unreadable_values_are_listed_and_masked(self):
        parsed = self.parsed(quiz=("abc", CARA + "0", "", "1"))
        result = plan(parsed, {"blank_as_missing": True})
        quiz = self.keys(parsed)["Quiz  1"]
        self.assertEqual([(entry["name"], entry["raw"]) for entry in result["unreadable"]],
                         [("Example, Alice", "abc"), ("Example, Bob", "…")])
        self.assertEqual(result["columns"][0]["counts"]["unreadable"], 2)
        self.assertEqual(result["counts"]["unreadable"], 2)
        self.assertEqual([change["name"] for change in result["changes"] if change["column"] == quiz], ["Example, Cara"])
        self.assertNotIn(CARA, json.dumps(result))

    def test_fingerprint_is_stable_and_sensitive(self):
        parsed = self.parsed()
        cells = {"item_q1": {ROW[ALICE]: cell(11)}}
        first = plan(parsed, None, cells)
        self.assertEqual(first["fingerprint"], plan(self.parsed(), None, {"item_q1": {ROW[ALICE]: cell(11)}})["fingerprint"])
        # The options a plan returns normalize to themselves, so sending them back gives the same plan.
        self.assertEqual(imports.normalize_options(first["options"]), first["options"])
        self.assertEqual(plan(parsed, json.dumps(first["options"]), cells)["fingerprint"], first["fingerprint"])
        explicit = {"mapping": {column["key"]: column["mapping"] for column in first["columns"]}}
        variants = [plan(parsed, None, {"item_q1": {ROW[ALICE]: cell(15)}}),  # the cell changed since the preview
                    plan(parsed, None, cells, revision=5),
                    plan(parsed, {"replace": [ROW[CARA]]}, cells),
                    plan(parsed, {"blank_as_missing": True}, cells),
                    plan(parsed, explicit, cells),
                    plan(self.parsed(quiz=("8", "", "7", "8")), None, cells),
                    plan(parsed, None, cells, students=STUDENTS[:2])]
        prints = {first["fingerprint"], *(variant["fingerprint"] for variant in variants)}
        self.assertEqual(len(prints), len(variants) + 1)

    def test_new_columns_that_would_pass_the_column_limit_are_not_ready(self):
        # Review fix: the preview said ready and the apply then refused "at most 500 columns"; archived columns count too.
        items = [{"id": f"item_{n}", "title": f"Q{n}", "points": "1", "archived": n % 2 == 0, "sections": []} for n in range(499)]
        parsed = imports.parse(csv_bytes([["UIN", "A", "B"], [ALICE, "1", "2"]]), "sheet.csv")
        options = {"mapping": {"c1": {"action": "new", "title": "A", "points": "5"}, "c2": {"action": "new", "title": "B", "points": "5"}}}
        result = plan(parsed, options, setup={"categories": [], "items": items})
        self.assertFalse(result["ready"])
        self.assertTrue(any("at most 500" in warning for warning in result["warnings"]), result["warnings"])
        options["mapping"]["c2"] = {"action": "skip"}
        self.assertTrue(plan(parsed, options, setup={"categories": [], "items": items})["ready"])

    def test_mapping_validation(self):
        parsed = self.parsed()
        keys = self.keys(parsed)
        base = {key: {"action": "skip"} for key in keys.values()}

        def refused(changes, message):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                plan(parsed, {"mapping": {**base, **changes}})

        refused({keys["Lab"]: {"action": "item", "item_id": "item_gone"}}, "no longer exists")
        refused({keys["Lab"]: {"action": "item", "item_id": "item_old"}}, "archived")
        refused({keys["Lab"]: {"action": "item", "item_id": "item_q1"}, keys["Quiz  1"]: {"action": "item", "item_id": "item_q1"}},
                "both go to “Quiz 1”")
        refused({keys["Lab"]: {"action": "new", "title": " quiz 1 ", "points": "3"}}, "already has a column titled “Quiz 1”")
        refused({keys["Lab"]: {"action": "new", "title": "Old  quiz", "points": "3"}}, "already has a column")
        refused({keys["Lab"]: {"action": "new", "title": "X", "points": "3"}, keys["Reading"]: {"action": "new", "title": "x", "points": "1"}},
                "Two new columns")
        refused({keys["Lab"]: {"action": "new", "title": "X", "points": "3", "category_id": "cat_gone"}}, "category")
        refused({"c999": {"action": "skip"}}, "does not have")
        with self.assertRaisesRegex(ValueError, "Choose what to do with the file's “Lab”"):
            plan(parsed, {"mapping": {key: value for key, value in base.items() if key != keys["Lab"]}})
        skipped = plan(parsed, {"mapping": base})
        self.assertEqual((skipped["changes"], skipped["new_items"], skipped["ready"]), ([], [], True))

    def test_options_validation(self):
        self.assertEqual(imports.normalize_options(None), {"mapping": None, "replace": [], "blank_as_missing": False})
        self.assertEqual(imports.normalize_options(""), imports.normalize_options("null"))
        normalized = imports.normalize_options(json.dumps({
            "mapping": {"c9": {"action": "new", "title": "  Homework\n 2 ", "points": 12.5, "extra": 1}, "c5": {"action": "skip"}},
            "replace": [ROW[CARA], ROW[ALICE], ROW[CARA]], "blank_as_missing": True}))
        self.assertEqual(normalized, {"mapping": {"c5": {"action": "skip"},
                                                  "c9": {"action": "new", "title": "Homework 2", "category_id": None, "points": "12.5"}},
                                      "replace": [ROW[ALICE], ROW[CARA]], "blank_as_missing": True})
        self.assertEqual(imports.normalize_options(normalized), normalized)
        for value, message in [("{", "could not be read"), ("[]", "could not be read"), ("x" * (64 * 1024 + 1), "too large"),
                               ({"mapping": {}, "blank_as_mising": True}, "Unknown import option"),
                               ({"replace": ["1234567"]}, "roster"), ({"replace": ROW[ALICE]}, "Replace"),
                               ({"blank_as_missing": "yes"}, "true or false"),
                               ({"mapping": {"c1": {"action": "delete"}}}, "is skipped"),
                               ({"mapping": {"c1": "skip"}}, "object"),
                               ({"mapping": {"c1": {"action": "item"}}}, "Choose the gradebook column"),
                               ({"mapping": {"c1": {"action": "new", "title": " ", "points": "1"}}}, "title"),
                               ({"mapping": {"c1": {"action": "new", "title": "T" * 121, "points": "1"}}}, "title"),
                               ({"mapping": {"c1": {"action": "new", "title": "T", "points": "0"}}}, "more than 0"),
                               ({"mapping": {"c1": {"action": "new", "title": "T", "points": "-1"}}}, "points"),
                               ({"mapping": {"c1": {"action": "new", "title": "T", "points": "1.00001"}}}, "ten-thousandths"),
                               ({"mapping": {"": {"action": "skip"}}}, "could not be read")]:
            with self.subTest(value=str(value)[:60]), self.assertRaisesRegex(ValueError, message):
                imports.normalize_options(value)


if __name__ == "__main__":
    unittest.main()
