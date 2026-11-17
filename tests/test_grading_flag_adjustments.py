"""GRADING FLAG REVIEW DEDUCTIONS: synthetic rubric amounts, contradictions and provider-neutral deferral."""
import unittest
from unittest.mock import patch

from grading import flag_adjustments as deductions, flag_resolution as fr
from grading.scoring import score
from rosters import connect
from fake_openai import flag_resolution_answer, payload_of
import test_grading_flag_resolution as fixture


class Amounts(unittest.TestCase):
    def setUp(self):
        item = {"id": "R1", "points": .5, "requires": [], "excludes": [], "stackable": True, "group": None,
                "clarification": 'When the condition applies, add the review reason "Coordinator deduction: take off 0.25 point for an appended inequality."'}
        self.rubric = {"mode": "positive", "floor": 0, "ceiling": 3, "groups": [], "items": [item]}
        self.assessment = {"applications": [{"rubric_item_id": "R1", "applied": True}]}
        self.row = {"rubric": self.rubric, "content": {"max_points": 3}, "deduction_rules": deductions.rules_of(self.rubric),
                    "existing": {"standard.primary": self.assessment}}
        self.ruling = {"decision": "adopt", "adopt": "standard.primary", "reason": "The appended inequality meets the approved condition."}
        self.raw = {"expected_score": .25, "deductions": [{"id": self.row["deduction_rules"][0]["id"], "applied": True,
                                                         "reason": "The inequality is appended to the objective."}]}

    def test_only_explicit_additional_amounts_become_choices_and_negative_items_are_not_duplicated(self):
        self.rubric["items"] += [
            {"id": "R2", "points": -1, "description": "Coordinator deduction: take off 1 point."},
            {"id": "R3", "points": 0, "description": "Ask the instructor about a deduction."},
            {"id": "bonus", "points": 0, "description": "Apply an additional 2-point credit for showing the work."},
            {"id": "R4", "points": 0, "description": "Apply an additional 0.1-point deduction for a missing zero."}]
        rules = deductions.rules_of(self.rubric)
        self.assertEqual([(r["item_id"], r["points"]) for r in rules], [("R1", -.25), ("R4", -.1)])
        self.assertIn("When the condition applies", rules[0]["condition"])
        self.rubric["items"][0]["notes"] = self.rubric["items"][0]["clarification"]
        self.assertEqual(len(deductions.rules_of(self.rubric)), 2)

    def test_authorized_amount_is_applied_to_uphold_and_retick(self):
        for decision in ["adopt", "revise"]:
            ruling = {**self.ruling, "decision": decision, "assessment": self.assessment}
            deductions.validate(self.raw, self.row, ruling)
            adjusted = score(self.rubric, self.assessment["applications"], 3, ruling["scoring"]["adjustments"])
            self.assertEqual(adjusted["score"], .25)
            self.assertFalse(deductions.mismatch(ruling["scoring"], adjusted))

    def test_inapplicable_deduction_keeps_the_original_score(self):
        self.raw.update(expected_score=.5)
        self.raw["deductions"][0]["applied"] = False
        deductions.validate(self.raw, self.row, self.ruling)
        self.assertEqual(self.ruling["scoring"]["adjustments"], [])

    def test_omitted_unknown_duplicate_and_model_chosen_amounts_are_refused(self):
        original = self.raw["deductions"][0]
        for entries in [[], [original, original], [{**original, "id": "invented"}], [{**original, "points": -.5}],
                        [{**original, "applied": "yes"}], [{**original, "reason": ""}]]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                deductions.validate({**self.raw, "deductions": entries}, self.row, dict(self.ruling))

    def test_missing_infinite_boolean_or_inconsistent_totals_are_refused(self):
        for expected in [None, True, float('nan'), float('inf'), .5, -.25]:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                deductions.validate({**self.raw, "expected_score": expected}, self.row, dict(self.ruling))

    def test_prose_total_and_unapplied_prose_deduction_cannot_contradict_the_submission(self):
        for reason in ["The final grade is 0.5/3.", "Award 0.5 points.", "Final score: 0.5."]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, "explanation contradicts"):
                deductions.validate(self.raw, self.row, {**self.ruling, "reason": reason})
        raw = {"expected_score": .5, "deductions": [{**self.raw["deductions"][0], "applied": False}]}
        with self.assertRaisesRegex(ValueError, "describes a deduction"):
            deductions.validate(raw, self.row, {**self.ruling, "reason": "Coordinator deduction: take off 0.25 point."})

    def test_student_fraction_and_uncertain_instructor_decision_are_not_final_score_claims(self):
        deductions.validate(self.raw, self.row, {**self.ruling, "reason": "The student wrote 1/3 in the work."})
        deductions.validate(self.raw, self.row, {**self.ruling, "reason": "Component A earns 0.5 points. Final score is 0.25/3."})
        for reason in ["If the mark is a slash, award 2/3; otherwise award 1/3.", "Please apply the 0.25 deduction manually."]:
            ruling = {"decision": "unreliable", "reason": reason}
            deductions.validate({}, self.row, ruling)
            self.assertNotIn("scoring", ruling)

    def test_two_authorized_conditions_and_floor_are_scored_with_decimal_arithmetic(self):
        self.rubric["items"].append({**self.rubric["items"][0], "id": "R2", "points": 0,
                                     "clarification": "Coordinator deduction: take off 0.3 point for a missing zero."})
        self.row["deduction_rules"] = deductions.rules_of(self.rubric)
        self.raw = {"expected_score": 0, "deductions": [{"id": r["id"], "applied": True, "reason": "This condition applies."}
                                                       for r in self.row["deduction_rules"]]}
        deductions.validate(self.raw, self.row, self.ruling)
        self.assertEqual(len(self.ruling["scoring"]["adjustments"]), 2)

    def test_finalization_checks_adjustments_even_when_the_floor_hides_a_missing_one(self):
        record = {"flags": [], "scoring": {"expected_score": 0, "adjustments": [{"points": -.25, "reason": "Required"}]}}
        output = {"state": "proposed", "score": {"score": 0, "adjustments": []}, "reasons": [],
                  "signals": {"risk": {"blocks": [], "individual": False}, "committee": {"status": "resolved"}}}
        held = fr.apply(output, record)
        self.assertEqual(held["state"], "grading_review")
        self.assertEqual(held["signals"]["flag_resolution"]["outcome"], "unsettled")

    def test_one_contradictory_ruling_does_not_reject_another_response(self):
        rows = [{**self.row, "ref": ref, "labels": ["standard.primary"], "flags": [], "images": [], "graded": lambda value: value}
                for ref in ["R1", "R2"]]
        good = {**self.raw, "decision": "uphold", "uphold": "standard.primary", "transcript_changes": [], "flag_rulings": []}
        found, _, _ = fr.rulings_of({"rulings": [{**good, "ref": "R1", "expected_score": .5}, {**good, "ref": "R2"}]}, rows)
        self.assertTrue(found["R1"]["failed"])
        self.assertEqual(found["R2"]["scoring"]["expected_score"], .25)


class Pipeline(fixture.FlagResolution):
    def authorized(self):
        original = self.frq._resolution_rows
        def rows(snapshot, *rest):
            found = original(snapshot, *rest)
            for row in found:
                row["guidance"] = {**(row["guidance"] or {}), "pairs": (row["guidance"] or {}).get("pairs", []),
                                   "clarifications": {**(row["guidance"] or {}).get("clarifications", {}),
                                                      "R1": "Coordinator deduction: take off 0.25 point when an inequality is appended."}}
            return found
        self.addCleanup(patch.stopall)
        patch.object(self.frq, "_resolution_rows", side_effect=rows).start()

    def test_deduction_survives_the_carrier_and_automatic_acceptance(self):
        self.held(); self.authorized()
        expected = []
        def answer(payload):
            result = flag_resolution_answer(payload, adopt="standard.second")
            for ruling in result["rulings"]:
                ruling["deductions"][0]["applied"] = True
                ruling["expected_score"] -= .25
                expected.append(ruling["expected_score"])
            return result
        _, done = self.resolve(answer)
        self.assertEqual(done["totals"]["adopted"], 1)
        with connect(self.path) as db:
            rid = db.execute("SELECT response_id FROM frq_tasks WHERE job_id=?", (done["jobs"][0],)).fetchone()[0]
        after = self.frq.response(rid)
        self.assert_finalized(after, "flag_resolution")
        self.assertEqual(after["result"]["score"]["score"], expected[-1])
        self.assertEqual([a["points"] for a in after["result"]["score"]["adjustments"]], [-.25])
        self.assertEqual(after["result"]["signals"]["flag_resolution"]["scoring"]["expected_score"], expected[-1])
        self.assertEqual(len(self.sent()), 1)

    def test_a_changed_point_value_is_recalculated_on_the_ruling_and_its_deduction(self):
        """RECALCULATING KEEPS A FLAG RULING (2026-10-26): the ruling's lines and deduction on today's points, accepted as before."""
        self.held(); self.authorized()
        def answer(payload):
            result = flag_resolution_answer(payload, adopt="standard.second")
            for ruling in result["rulings"]:
                ruling["deductions"][0]["applied"] = True
                ruling["expected_score"] -= .25
            return result
        _, done = self.resolve(answer)
        with connect(self.path) as db:
            rid = db.execute("SELECT response_id FROM frq_tasks WHERE job_id=?", (done["jobs"][0],)).fetchone()[0]
        question = self.frq.assignment(self.plan["id"])["questions"][0]
        self.frq.edit_rubric(self.plan["id"], self.qid, {"version": question["version"], "rubric_id": question["rubric"]["id"],
                                                         "sha256": question["rubric"]["sha256"], "lines": [{"item_id": "R1", "points": 9}]})
        self.queue(operation="rescore", response_ids=[rid])
        self.run_all()
        after = self.frq.response(rid)
        self.assert_finalized(after, "flag_resolution")
        self.assertEqual((after["result"]["score"]["score"], [a["points"] for a in after["result"]["score"]["adjustments"]]), (8.75, [-.25]))
        self.assertEqual(after["result"]["signals"]["flag_resolution"]["scoring"]["expected_score"], 8.75)
        self.assertEqual(len(self.sent()), 1)  # Nothing was asked again.

    def test_contradiction_cannot_create_a_carrier_grade(self):
        before = self.held(); self.authorized()
        def answer(payload):
            result = flag_resolution_answer(payload, adopt="standard.second")
            result["rulings"][0]["expected_score"] -= .25
            result["rulings"][0]["reason"] = "The final grade includes the required deduction."
            return result
        _, done = self.resolve(answer)
        self.assertEqual((done["totals"]["failed"], done["jobs"]), (1, []))
        self.assertEqual(self.frq.response(before["id"])["current_result_id"], before["current_result_id"])

    def test_opus_style_manual_deduction_request_remains_with_instructor(self):
        before = self.held(); self.authorized()
        def answer(payload):
            result = flag_resolution_answer(payload, "unreliable")
            result["rulings"][0]["reason"] = "Apply the coordinator's 0.25 deduction manually; the mark remains uncertain."
            result["rulings"][0].pop("expected_score")
            result["rulings"][0].pop("deductions")
            return result
        _, done = self.resolve(answer)
        self.assertEqual((done["totals"]["unreliable"], done["jobs"]), (1, []))
        self.assertEqual(self.frq.response(before["id"])["current_result_id"], before["current_result_id"])

    def test_existing_review_keeps_its_old_prompt_and_needs_no_new_fields(self):
        before = self.held()
        seen = []
        def answer(body, host):
            payload = payload_of(body)
            if "responses" in payload:
                seen.append(payload)
                return flag_resolution_answer(payload)
            from fake_openai import grade_answer
            return grade_answer(payload)
        self.host.script = answer
        with patch.object(fr, "RULE", "flag-resolution-v2"):
            self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "old-review"})
        self.frq.process_cloud(); self.run_all()
        done = self.frq.flag_resolution(self.plan["id"])["resolution"]
        self.assertEqual(done["status"], "complete")
        self.assertTrue(seen)
        self.assertNotIn("deduction_rules", seen[0])
        self.assertEqual(self.frq.response(before["id"])["result"]["signals"]["flag_resolution"]["rule"], "flag-resolution-v2")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in [Amounts, Pipeline]:
        for name in loader.getTestCaseNames(case):
            if name in vars(case): suite.addTest(case(name))
    return suite
