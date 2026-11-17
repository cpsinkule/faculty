"""GRADING "PART CAPS" (requirement, 2026-10-15). A deducting rubric's lettered parts are line groups whose floor is minus
the part's worth; scoring stops each part there, and Gradescope, which cannot cap a part, gets what the cap gave back as the grade's
point adjustment. Their own Quiz 3 problem 3 is the example: Part A worth 0.5, Part B worth 1.5 with five -0.5 lines."""
import unittest
from decimal import Decimal

from grading import gradescope_web as gw, rubrics
from grading.gradescope_sync import GradescopeSender, expected
from grading.quiz_drafts import version_rules
from grading.rubric_generation import adapted_rules, family_rubric, uncapped_parts, written_rubric
from grading.scoring import score

LINES = [("R1", "Correct answer with supporting work: (a) bounded; (b) $$(0,0),\\ (0,10),\\ (12,4)$$", 0),
         ("R2", "Part A: The answer is not \"bounded\"", -0.5),
         ("R3", "Part B: The corner point $$(0,0)$$ is missing", -0.5),
         ("R4", "Part B: The corner point $$(0,10)$$ is missing or incorrect", -0.5),
         ("R5", "Part B: The corner point $$(12,4)$$ is missing or incorrect", -0.5),
         ("R6", "Part B: No algebraic work for $$(12,4)$$", -0.5),
         ("R7", "Part B: The list includes a point that is not a corner", -0.5)]
WORTHS = {"a": 0.5, "b": 1.5}


def lines():
    return [{"id": i, "description": d, "points": p} for i, d, p in LINES]


def capped():
    return rubrics.validate(rubrics.capped_parts({"mode": "negative", "items": lines()}, WORTHS, 2), 2)


def applied(*ids):
    return [{"rubric_item_id": i, "applied": True} for i in ids]


class PartCaps(unittest.TestCase):
    def test_each_part_is_one_cap_at_its_worth_and_whole_question_lines_stay_outside(self):
        rules = capped()
        self.assertEqual(rules["groups"], [
            {"id": "part-a-cap", "description": "Part A", "selection": "multiple", "floor": -0.5, "ceiling": 0},
            {"id": "part-b-cap", "description": "Part B", "selection": "multiple", "floor": -1.5, "ceiling": 0}])
        self.assertEqual([i["group"] for i in rules["items"]], [None, "part-a-cap"] + ["part-b-cap"] * 5)
        self.assertEqual(rubrics.part_caps_of(rules), [{"part": "A", "deductions_stop_at": 0.5}, {"part": "B", "deductions_stop_at": 1.5}])

    def test_part_b_no_longer_eats_part_a(self):
        # Their example: Part A right, Part B missing (0,0), a wrong (12,4) read off the graph, no algebra, and (20,0) listed.
        rules, wrong = capped(), applied("R3", "R5", "R6", "R7")
        self.assertEqual(score(rubrics.validate({"mode": "negative", "items": lines()}, 2), wrong, 2)["score"], 0.0)  # uncapped: 2 - 2.0
        graded = score(rules, wrong, 2)
        self.assertEqual((graded["score"], graded["part_caps"]), (0.5, 0.5))
        self.assertIn("Part B's deductions stopped at its 1.5 points.", graded["notes"])
        # Within its worth a part is untouched, and a grade no cap changed carries no `part_caps` (its score is as it always was).
        self.assertNotIn("part_caps", score(rules, applied("R3", "R5"), 2))
        self.assertEqual(score(rules, applied("R3", "R4", "R5"), 2)["score"], 0.5)  # Part B loses exactly its worth; Part A keeps its 0.5
        # Every line of both parts: each part loses exactly its worth, so the score floors at 0 the same way.
        self.assertEqual(score(rules, applied(*[i for i, _, p in LINES if p]), 2)["score"], 0.0)

    def test_positive_rubrics_one_part_and_unknown_worths(self):
        positive = {"mode": "positive", "items": [{"id": "R1", "description": "Part A: right", "points": 1}]}
        self.assertIs(rubrics.capped_parts(positive, WORTHS, 2), positive)
        single = {"mode": "negative", "items": [{"id": "R1", "description": "Part A: wrong", "points": -2}]}
        self.assertIs(rubrics.capped_parts(single, {"a": 2}, 2), single)  # one part is the whole question
        plain = {"mode": "negative", "items": [{"id": "R1", "description": "Wrong", "points": -2}]}
        self.assertIs(rubrics.capped_parts(plain, {}, 2), plain)
        for worths in ({}, {"a": 0.5, "b": 1}, {"a": 0.5, "c": 1.5}):
            with self.subTest(worths=worths):
                left = rubrics.capped_parts({"mode": "negative", "items": lines()}, worths, 2)
                self.assertNotIn("groups", left)
                self.assertEqual(left["warnings"], [rubrics.PARTS_UNCAPPED])
        # A printed worth wins over the writer's; a part with neither leaves the parts unknown.
        self.assertEqual(rubrics.part_worths([{"label": "a", "points": 0.5}, {"label": "b", "points": None}], {"a": 1, "b": 1.5}), WORTHS)
        self.assertEqual(rubrics.part_worths([{"label": "a", "points": None}, {"label": "b", "points": 1.5}], {}), {})

    def test_a_line_follows_its_wording_and_caps_are_well_formed(self):
        rules = capped()
        reworded = [{**i, "description": "Part A: " + i["description"][8:]} if i["id"] == "R7" else i for i in rules["items"]]
        self.assertEqual(rubrics.validate({**rules, "items": reworded}, 2)["items"][-1]["group"], "part-a-cap")
        unlettered = [{**i, "description": "Lists a point that is not a corner"} if i["id"] == "R7" else i for i in rules["items"]]
        self.assertIsNone(rubrics.validate({**rules, "items": unlettered}, 2)["items"][-1]["group"])
        for bad in ({"mode": "positive"}, {"groups": [{**g, "ceiling": 0.5} for g in rules["groups"]]},
                    {"groups": [{**g, "selection": "single"} for g in rules["groups"]]}, {"groups": [{**g, "floor": 0} for g in rules["groups"]]}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "PART CAPS"):
                rubrics.validate({**rules, "items": [{**i, "points": -i["points"]} for i in rules["items"]] if bad.get("mode") == "positive" else rules["items"], **bad}, 2)
        # A cap is no rule about the line itself, so it never makes a condition structured.
        conditional = [{**i, "description": i["description"] + " only if the work is shown", "condition_handling": "structured"} if i["id"] == "R6" else i
                       for i in rules["items"]]
        with self.assertRaisesRegex(ValueError, "To mark a condition as structured"):
            rubrics.validate({**rules, "items": conditional}, 2)

    def test_a_capped_grade_goes_to_gradescope_with_what_the_cap_gave_back(self):
        rules = capped()
        order = [{"id": n, "points": Decimal(str(abs(p))), "group_id": None, "exclusive": False} for n, (_, _, p) in enumerate(LINES, 1)]
        mapping = {"question": {"weight": 2, "scoring_type": "negative"}, "order": order, "items": {i: order[n] for n, (i, _, _) in enumerate(LINES)}}
        final = {"score": score(rules, applied("R3", "R5", "R6", "R7"), 2)}
        (chosen, adjustment, gradescope), reason = expected(mapping, final)
        self.assertIsNone(reason)
        self.assertEqual((chosen, adjustment, gradescope), ([3, 5, 6, 7], Decimal("0.5"), Decimal("0.5")))
        self.assertEqual(gw.gradescope_score(mapping["question"], order, set(chosen), adjustment), Decimal("0.5"))
        # A grade the cap left alone sends no adjustment, as before.
        (_, unchanged, _), _ = expected(mapping, {"score": score(rules, applied("R3"), 2)})
        self.assertEqual(unchanged, Decimal(0))
        # The receipt of that send is the same content as the grade, the cap's give-back counted with its adjustments.
        receipt = {"items_json": '{"lines": {"R3": 3, "R5": 5, "R6": 6, "R7": 7}, "chosen": [3, 5, 6, 7]}', "adjustment": "0.5", "expected_score": "0.5"}
        decision = {"kind": "review", "selected": ["R3", "R5", "R6", "R7"], "adjustments": [], "score": "0.5", "part_caps": "0.5"}
        self.assertTrue(GradescopeSender._same_content(receipt, decision))
        self.assertFalse(GradescopeSender._same_content(receipt, {**decision, "part_caps": None}))

    def test_drafts_carry_the_writers_worths_and_say_when_parts_cannot_be_capped(self):
        problem = {"number": "3", "points": 2, "parts": [{"label": "a", "points": None}, {"label": "b", "points": None}]}
        answer = {"points": 2, "parts": [{"part": "(a)", "points": 0.5}, {"part": "B", "points": 1.5}],
                  "items": [{"description": d, "points": p} for _, d, p in LINES]}
        drafted = written_rubric(answer, problem, "negative")
        self.assertEqual(drafted["part_points"], WORTHS)
        self.assertEqual(rubrics.part_worths(problem["parts"], drafted["part_points"]), WORTHS)
        self.assertFalse(any("parts' points" in w for w in drafted["warnings"]))
        unknown = written_rubric({**answer, "parts": []}, problem, "negative")
        self.assertNotIn("part_points", unknown)
        self.assertIn("so one part's deductions could take points another part earned", unknown["warnings"][-1])
        self.assertIsNone(uncapped_parts(problem["parts"], {}, 2, [d for _, d, _ in LINES], "positive"))
        # All versions at once: one list of worths, shared like the lines' points.
        family = {"id": "p3", "versions": [{"version": v, "number": "3", "points": 2, "parts": problem["parts"]} for v in "AB"]}
        together = family_rubric({"points": 2, "parts": answer["parts"], "items": [{"points": p, "descriptions": [{"version": v, "description": d} for v in "AB"]}
                                                                                   for _, d, p in LINES]}, family, "negative", ["A", "B"])
        self.assertEqual(together["part_points"], WORTHS)

    def test_quiz_versions_and_adaptations_are_capped_and_a_makeup_keeps_its_originals_rules(self):
        quiz = {"items": [{"points": p, "descriptions": {"A": d}} for _, d, p in LINES]}
        _, rules = version_rules(quiz, "A", "negative", 2, [{"label": "a", "points": 0.5}, {"label": "b", "points": 1.5}])
        self.assertEqual(rules["groups"], capped()["groups"])
        self.assertEqual(version_rules(quiz, "A", "negative", 2)[1]["groups"], [])  # no parts, as before
        uncapped = rubrics.validate({"mode": "negative", "items": lines()}, 2)
        accepted = [{"source": i, "description": d, "points": p} for i, d, p in LINES]
        self.assertEqual(adapted_rules(uncapped, accepted, 2, 2, [], worths=WORTHS)["groups"], capped()["groups"])
        self.assertEqual(adapted_rules(uncapped, accepted, 2, 2, [])["groups"], [])  # a makeup of an uncapped original
        self.assertEqual(adapted_rules(capped(), accepted, 2, 2, [])["groups"], capped()["groups"])  # ... and of a capped one
        self.assertEqual(adapted_rules(capped(), accepted, 2, 2, [], worths={})["groups"], capped()["groups"])  # worths unknown: kept


if __name__ == "__main__":
    unittest.main()
