"""Synthetic equivalents of per-question Gradescope evaluation exports."""
import copy
import csv
import io
import json
import unittest
from unittest.mock import patch

from grading import config, gradescope_import, recognition, rubrics
from grading.models import plain_transcript
from grading.providers.local_vlm import LocalVlmProvider
from grading.scoring import score


HEADER = ["Assignment Submission ID", "Question Submission ID", "First Name", "Last Name",
          "SID", "Email", "Sections", "Score", "Submission Time"]
TRAILER = ["Adjustment", "Comments", "Grader", "Tags"]
EXAMPLES = [
    (4, [4, 2, 1, 1, 0, 2, 1, 1, 1, 0, 0]),
    (2, [2, .75, .75, .5, 1, 0]),
    (2, [2, 1, 1, .5, 1, 0]),
    (4, [4, 1, 1, 1, .5, 1, 0]),
    (6, [6, 1, 1, 2, 1.5, 1, 1, 1, .5, 1, .5, 1, 0]),
    (8, [8, 6, 1, 1, 1, 1, .25, .25, .25, .25, 1, 0, 2, 1, 1, .5, 0, 0]),
    (6, [6, 1, 1, 1.5, .75, 1.5, .75, 1, .5, .5, 2.5, 0]),
    (8, [8, .5, .5, 1, .5, 1, .5, 1, 1, 1, 1, .5, 1, .5, 2, 0]),
]


def encode(rows):
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    return output.getvalue()


def export_rows(maximum=10, points=(10, .75, .25, 0), descriptions=None, question=1):
    descriptions = descriptions or ["Full credit"] + [f'Part credit {i}: $$x_{{{i}}}=\\frac{{1}}{{2}}$$, "shown"\nsecond line  '
        for i in range(1, len(points) - 1)] + ["No answer"]
    return [HEADER + descriptions + TRAILER, [],
        ["Point Values", "", "", "", "", str(maximum), *map(str, points)],
        ["Rubric Numbers", "", "", "", "", "", *[f"{question}.{i+1}" for i in range(len(points))]],
        ["Scoring Method", "positive"]]


class GradescopeContracts(unittest.TestCase):
    def test_a_positive_rubric_keeps_a_deduction_and_scores_it_as_gradescope_does(self):
        """GRADING "DEDUCTIONS IN A POSITIVE RUBRIC" (2026-10-25: the coordinator's -1 lines): credits add from zero, a checked -1 is
        taken off, and the score stays between 0 and the question's points."""
        rows = export_rows(8, [8, 4, 0, -1], descriptions=["Full credit", "Half credit", "No answer", "Corner Points are not written as ordered pairs"])
        rules = rubrics.parse(encode(rows), 8, "csv")
        self.assertEqual((rules["mode"], [i["points"] for i in rules["items"]]), ("positive", [8, 4, 0, -1]))
        applied = lambda *ids: [{"rubric_item_id": i["id"], "applied": i["id"] in ids} for i in rules["items"]]
        self.assertEqual(score(rules, applied("R1_1", "R1_4"), 8)["score"], 7)
        self.assertEqual(score(rules, applied("R1_2", "R1_4"), 8)["score"], 3)
        self.assertEqual(score(rules, applied("R1_3", "R1_4"), 8)["score"], 0)  # the floor, as on Gradescope
        with self.assertRaises(ValueError):
            rubrics.parse(encode(export_rows(8, [8, -9], descriptions=["Full credit", "Too much"])), 8, "csv")
        with self.assertRaises(ValueError):  # a deducting rubric's lines stay at 0 or below
            rubrics.validate({"mode": "negative", "items": [{"id": "R1", "description": "Bonus", "points": 1}]}, 8)

    def test_all_eight_export_shapes_preserve_every_item(self):
        for q, (maximum, points) in enumerate(EXAMPLES, 1):
            with self.subTest(question=q):
                rows = export_rows(maximum, points, question=q)
                source = "\ufeff" + encode(rows)
                rules = rubrics.parse(source, maximum, "csv")
                self.assertEqual(rules["mode"], "positive")
                self.assertEqual(rules["ceiling"], maximum)
                self.assertEqual([i["description"] for i in rules["items"]], rows[0][9:-4])
                self.assertEqual([i["points"] for i in rules["items"]], points)
                self.assertEqual([i["id"] for i in rules["items"]], [f"R{q}_{i+1}" for i in range(len(points))])
                for i, item in enumerate(rules["items"]):
                    self.assertEqual(json.loads(item["original_source_text"]), {
                        "description": rows[0][9+i], "rubric_number": f"{q}.{i+1}", "point_value": str(points[i])})
                self.assertEqual(score(rules, [{"rubric_item_id": f"R{q}_1", "applied": True}], maximum)["score"], maximum)
                self.assertEqual(score(rules, [{"rubric_item_id": f"R{q}_{len(points)}", "applied": True}], maximum)["score"], 0)
                self.assertEqual(gradescope_import.summary(source)["item_count"], len(points))
                self.assertTrue(rules["warnings"])

    def test_student_rows_are_not_rubric_metadata(self):
        rows = export_rows()
        rows.insert(1, ["submission-fixture", "question-fixture", "Synthetic", "Student", "001234567",
            "synthetic@example.invalid", "501", "1", "time", "0", "1", "1", "0", "0", "Rubric Numbers", "grader", ""])
        rules = rubrics.parse(encode(rows), 10, "csv")
        self.assertNotIn("Synthetic", json.dumps(rules))
        self.assertNotIn("001234567", json.dumps(rules))
        self.assertEqual(len(rules["items"]), 4)

    def test_totals_must_match_instead_of_silent_scaling_or_summing(self):
        source = encode(export_rows(4, [4, 2, 2, 0]))
        for maximum in (2, 10):
            with self.subTest(maximum=maximum), self.assertRaisesRegex(ValueError, "worth 4 points.*never scaled"):
                rubrics.parse(source, maximum, "csv")
        self.assertEqual(rubrics.parse(source, 4, "csv")["ceiling"], 4)

    def test_negative_scoring_and_aligned_footer_remain_supported(self):
        rows = export_rows(10, [0, -1, -.5, -10])
        rows[-1][1] = "negative"
        rules = rubrics.parse(encode(rows), 10, "csv", "positive")
        self.assertEqual(rules["mode"], "negative")
        self.assertEqual(score(rules, [{"rubric_item_id": "R1_2", "applied": True}], 10)["score"], 9)
        rows[2] = ["Point Values", *([""] * 6), "10", "", "0", "-1", "-0.5", "-10", *([""] * 4)]
        rows[3] = ["Rubric Numbers", *([""] * 8), "1.1", "1.2", "1.3", "1.4", *([""] * 4)]
        self.assertEqual(rubrics.parse(encode(rows), 10, "csv"), rules)
        legacy = "Name,Error A,Error B\nSynthetic,1,0\nRubric Numbers,1.1,1.2\nPoint Values,-1,-2\n"
        self.assertEqual([i["points"] for i in rubrics.parse(legacy, 10, "csv")["items"]], [-1, -2])

    def test_a_course_without_sections_exports_no_sections_column(self):
        rows = export_rows(6, [6, 3, 1, .5, .25, 0])
        rows[0].remove("Sections")
        rules = rubrics.parse(encode(rows), 6, "csv")
        self.assertEqual([i["description"] for i in rules["items"]], rows[0][8:-4])
        self.assertEqual([i["points"] for i in rules["items"]], [6, 3, 1, .5, .25, 0])
        self.assertEqual(gradescope_import.summary(encode(rows))["layout"], "compact_footer")
        rows[2] = ["Point Values", *([""] * 5), "6", "", "6", "3", "1", "0.5", "0.25", "0", *([""] * 4)]
        rows[3] = ["Rubric Numbers", *([""] * 7), *[f"1.{i}" for i in range(1, 7)], *([""] * 4)]
        self.assertEqual(rubrics.parse(encode(rows), 6, "csv")["items"], rules["items"])
        rows = export_rows(6, [6, 3, 1, .5, .25, 0]); rows[0].remove("Sections"); rows[0].insert(9, "Extra item")
        with self.assertRaises(ValueError):
            rubrics.parse(encode(rows), 6, "csv")

    def test_ambiguous_or_incomplete_exports_fail_without_losing_items(self):
        malformed = []
        for row, column, value in [(2, 5, ""), (2, 5, "nan"), (2, 7, ""), (2, 7, "inf"),
                (3, 7, "1.1"), (3, 7, ""), (3, 7, "2.1"), (3, 7, "invalid"),
                (4, 1, "mixed"), (0, 10, ""), (0, -1, "unknown")]:
            rows = export_rows()
            rows[row][column] = value
            malformed.append(rows)
        rows = export_rows(); rows[2].append("1"); malformed.append(rows)
        rows = export_rows(); rows[2].pop(); malformed.append(rows)
        rows = export_rows(); rows[0].insert(9, "Extra item"); malformed.append(rows)
        rows = export_rows(); rows[3].insert(1, ""); malformed.append(rows)
        rows = export_rows(); rows.append(rows[-1]); malformed.append(rows)
        rows = export_rows(); rows.pop(3); malformed.append(rows)
        for i, rows in enumerate(malformed):
            with self.subTest(case=i), self.assertRaises(ValueError):
                rubrics.parse(encode(rows), 10, "csv")

    def test_descriptive_wording_is_not_a_conditional_rule(self):
        # "but", "either" and "no work" describe the student's work. Flagging
        # them marked whole rubrics conditional and forced every response to review.
        descriptions = ["Part A: Correct number of rows, but not columns",
            "Correct Answer, but no work to justify answer.",
            "Written in point-slope form but either the slope or the point does not match the work.",
            "No Answers for either parts a or b."]
        rules = rubrics.parse(encode(export_rows(descriptions=descriptions)), 10, "csv")
        self.assertEqual([item["exceptions"] for item in rules["items"]], [""]*4)
        self.assertEqual(len(rules["warnings"]), 1)
        self.assertIn("omit item groups", rules["warnings"][0])
        applications = [{"rubric_item_id": item["id"], "applied": index == 0, "confidence": .99,
            "evidence": [{"line_id": "line-1", "student_text": "x = 8", "crop": 0}]} for index, item in enumerate(rules["items"])]
        result = recognition.validate_grade({"applications": applications, "confidence": .99, "interpretation": {"error_graph": []}},
            plain_transcript("x = 8"), rules, 10, 1)
        self.assertEqual(result["review_reasons"], [])

    def test_a_rubric_imported_under_the_older_broader_rule_stops_flagging_every_response(self):
        # Real rubrics imported on the morning of 2026-09-19 carry the importer's mark on descriptive lines; revisions are immutable.
        from grading.recognition import RUBRIC_EXCEPTIONS
        described = "Correct Answer, but no work to justify answer."
        stale = {"id": "R5_12", "description": described, "exceptions": described}
        capped = {"id": "R5_9", "description": "At most one of the form items applies.", "exceptions": "At most one of the form items applies."}
        written = {"id": "R5_5", "description": "Used the correct formula for slope, but made an error.", "exceptions": "Never together with R5_4."}
        self.assertFalse(rubrics.conditional(stale))          # the importer's own mark, which today's rule no longer makes
        self.assertTrue(rubrics.conditional(capped))          # the importer's mark, and today's rule still finds the wording
        self.assertTrue(rubrics.conditional(written))         # a condition someone wrote always counts
        self.assertFalse(rubrics.conditional({"id": "R1", "description": "Correct", "exceptions": ""}))
        rules = rubrics.validate({"mode": "positive", "items": [{"id": "R1", "description": "Correct answer with work", "points": 6},
                                                                  {"id": "R2", "description": described, "points": 1, "exceptions": described}]}, 6)
        raw = {"applications": [{"rubric_item_id": "R1", "applied": True, "reason": "", "evidence": [{"line_id": "line-1", "student_text": "x = 8", "bbox": None, "crop": 0}], "origin_error_id": None, "confidence": .95},
                                {"rubric_item_id": "R2", "applied": False, "reason": "", "evidence": [], "origin_error_id": None, "confidence": .95}],
               "confidence": .95, "interpretation": {"error_graph": [], "final_answers": []}, "rubric_ambiguities": [], "review_reasons": []}
        self.assertNotIn(RUBRIC_EXCEPTIONS, recognition.validate_grade(raw, plain_transcript("x = 8"), rules, 6, 1)["review_reasons"])
        rules["items"][1]["exceptions"] = "Never together with R1."
        self.assertTrue(any("Rubric condition R2:" in r for r in recognition.validate_grade(raw, plain_transcript("x = 8"), rules, 6, 1)["review_reasons"]))

    def test_not_both_describing_the_work_is_not_a_conditional_rule(self):
        # rule-parser-v5 (2026-09-28): two drafted Quiz lines held 61 of 61 responses back for "not both" alone.
        work = ["Nonnegativity missing: the constraints $$x\\ge 0$$ and $$y\\ge 0$$ are not both stated (either one missing is enough for this deduction)",
                "A solution is stated, but it gives only the maximum value (such as $$P=56$$) or only the point (such as $$(4,9)$$), not both"]
        rules_ = ["Either this item or the next, but not both", "Award R3 or R4, not both", "Not both R3 and R4",
                  "Not both of these items", "Deduct 0.5 once for this and the line above, not both"]
        for text in work:
            self.assertFalse(rubrics.conditional({"id": "R7", "description": text, "exceptions": text}), text)
        for text in rules_:
            self.assertTrue(rubrics.conditional({"id": "R7", "description": text, "exceptions": text}), text)

    def test_wording_about_other_items_or_caps_still_needs_review(self):
        descriptions = ["Award unless the final answer item is selected", "Apply only if part a was attempted",
            "Either this item or the next, but not both", "Arithmetic slips: no more than 2 points in total"]
        rules = rubrics.parse(encode(export_rows(descriptions=descriptions)), 10, "csv")
        self.assertEqual([item["exceptions"] for item in rules["items"]], descriptions)
        self.assertEqual(sum("conditional rule" in warning for warning in rules["warnings"]), 4)

    def test_positive_follow_through_awards_share_an_error(self):
        descriptions = ["Full credit", "Correct slope based on the ordered pairs written down",
            "Correct equation with the slope from work", "No answer"]
        rules = rubrics.parse(encode(export_rows(descriptions=descriptions)), 10, "csv")
        for item in rules["items"][1:3]:
            self.assertTrue(item["follow_through"])
            self.assertFalse(item["repeat_penalty_for_same_originating_error"])
        applications = [{"rubric_item_id": item["id"], "applied": index in (1, 2), "origin_error_id": "E1",
            "confidence": .99, "evidence": [{"line_id": "line-1", "student_text": "x = 8", "crop": 0}]} for index, item in enumerate(rules["items"])]
        self.assertEqual(score(rules, applications, 10)["score"], 1)
        raw = {"applications": applications, "confidence": .99,
            "interpretation": {"error_graph": [{"id": "E1", "caused_by": [], "line_ids": ["line-1"], "description": "Early error"}]}}
        result = recognition.validate_grade(raw, plain_transcript("x = 8"), rules, 10, 1)
        self.assertEqual(result["score"]["score"], 1)
        self.assertEqual(result["review_reasons"], [])

    def test_compiler_preserves_exported_facts_and_explicit_follow_through(self):
        source = encode(export_rows(descriptions=["Full credit", "Answer based on work above", "Correct setup", "No answer"]))
        imported = rubrics.parse(source, 10, "csv")
        proposed = copy.deepcopy(imported)
        proposed["items"].reverse()
        for item in proposed["items"]:
            item.update(description="Paraphrase", original_source_text="Changed", follow_through=False)
        proposed["warnings"] = []
        result = rubrics.compiled(proposed, imported, 10)
        self.assertEqual(result, imported)
        for field, value in (("mode", "negative"), ("floor", 1), ("ceiling", 9)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                rubrics.compiled({**imported, field: value}, imported, 10)
        for mutation in ("points", "id", "missing"):
            invalid = copy.deepcopy(imported)
            if mutation == "missing": invalid["items"].pop()
            else: invalid["items"][1][mutation] = .5 if mutation == "points" else "changed"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                rubrics.compiled(invalid, imported, 10)

    def test_native_compiler_receives_only_metadata_and_constrained_ids(self):
        imported = rubrics.parse(encode(export_rows()), 10, "csv")
        info = {"details": {"quantization_level": "Q4_K_M"}, "capabilities": ["vision"]}
        raw = {"done": True, "done_reason": "stop", "message": {"content": json.dumps(imported)}}
        with patch("grading.providers.local_vlm.config.endpoint", return_value="http://127.0.0.1:11435/v1"), \
                patch("grading.providers.local_vlm.request_json", side_effect=[info, raw]) as transport:
            result = LocalVlmProvider(config.validate({"local_grader_backend": "ollama"}), "local_grader").compile("private student rows", 10, rubric=imported)
        body = transport.call_args.kwargs["body"]
        self.assertNotIn("private student rows", json.dumps(body))
        self.assertEqual(json.loads(body["messages"][1]["content"])["imported_rubric"], imported)
        entries = body["format"]["properties"]["items"]
        self.assertEqual((entries["minItems"], entries["maxItems"]), (4, 4))
        self.assertEqual(entries["items"]["properties"]["id"]["enum"], ["R1_1", "R1_2", "R1_3", "R1_4"])
        self.assertEqual(result["metadata"]["prompt"], "compile-v2.txt")
