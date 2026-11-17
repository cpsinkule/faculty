"""Fewer holds after the Exam 2 run (requirement, 2026-10-26): the graders take off the rubric's approved deductions
(GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS"), they are told which presentation the rubric does not name, a note a ruling settled
under one grader's name stays settled (GRADING "A SETTLED NOTE STAYS SETTLED"), and a grader's request on an empty answer leaves the
blank to Jev (GRADING "A GRADER'S REQUEST ON AN EMPTY ANSWER"). Synthetic data and the local fake host only."""
import json
import unittest
from pathlib import Path

from grading import arbitration, flag_resolution, grader_deductions, jev_blank, recognition, risk, rubric_quality, standard
from grading.providers.cloud import grading_schema
from grading.providers.local_vlm import VERSIONS
from grading.scoring import score
from fake_openai import arbitrate_answer, grade_answer, payload_of
from test_grading_pipeline import CORRECT, RULES
import test_grading_standard as standard_fixture

PROMPTS = Path(__file__).resolve().parent.parent / "grading" / "prompts"
DEDUCTION = "Coordinator deduction: take off 0.5 point for writing the answer as 6.0."
FROZEN = {"grader_deductions_rule": grader_deductions.RULE}


def rubric():
    return {"mode": "positive", "floor": 0, "ceiling": 10, "groups": [], "items": [
        {"id": "R1", "description": "Correct answer.", "points": 10, "requires": [], "excludes": [], "stackable": False, "group": None,
         "notes": DEDUCTION},
        {"id": "R2", "description": "No answer.", "points": 0, "requires": [], "excludes": [], "stackable": False, "group": None}]}


TRANSCRIPT = {"lines": [{"id": "line-1", "elements": [{"type": "math", "text": "x = 6.0", "latex": "x = 6.0", "state": "written"}]}],
              "plain_text": "x = 6.0", "latex": "x = 6.0", "confidence": .9, "ambiguities": [], "layout_relations": [], "reading_alternatives": []}


def answer(choices, applied=True):
    payload = {"transcript": TRANSCRIPT, "rubric": rubric(), "answer_parts": [""]}
    return {**grade_answer(payload, ("R1",)), "deductions": [{"id": c["id"], "applied": applied, "reason": "Written as 6.0."} for c in choices]}


class GraderDeductions(unittest.TestCase):
    def test_only_a_frozen_batch_offers_the_rubrics_stated_amounts(self):
        self.assertEqual(grader_deductions.offered(rubric(), {}), [])
        choices = grader_deductions.offered(rubric(), FROZEN)
        self.assertEqual([(c["item_id"], c["points"]) for c in choices], [("R1", -.5)])
        self.assertEqual(set(choices[0]), {"id", "item_id", "points", "condition"})
        plain = {**rubric(), "items": [{**item, "notes": ""} for item in rubric()["items"]]}
        self.assertEqual(grader_deductions.offered(plain, FROZEN), [])  # a rubric that states no amount grades as before

    def test_a_grader_decides_each_offered_deduction_and_the_scorer_takes_it_off(self):
        choices = grader_deductions.offered(rubric(), FROZEN)
        graded = recognition.validate_grade(answer(choices), TRANSCRIPT, rubric(), 10, 1, choices)
        self.assertEqual((graded["score"]["score"], graded["score"]["adjustments"][0]["points"]), (9.5, -.5))
        self.assertEqual([(d["item_id"], d["applied"]) for d in graded["deductions"]], [("R1", True)])
        self.assertEqual(grader_deductions.adjustments(graded), graded["score"]["adjustments"])  # what "Recalculate points" keeps
        kept = recognition.validate_grade(answer(choices, applied=False), TRANSCRIPT, rubric(), 10, 1, choices)
        self.assertEqual((kept["score"]["score"], kept["score"]["adjustments"]), (10, []))
        # Offered nothing, a grader's words about deductions are ignored, and its grade is what it always was.
        plain = recognition.validate_grade(answer(choices), TRANSCRIPT, rubric(), 10, 1)
        self.assertEqual((plain["score"]["score"], "deductions" in plain), (10, False))

    def test_an_unknown_repeated_malformed_or_missing_decision_breaks_the_contract(self):
        choices = grader_deductions.offered(rubric(), FROZEN)
        good = answer(choices)["deductions"][0]
        for entries in ([], [good, good], [{**good, "id": "D0"}], [{**good, "applied": "yes"}], [{**good, "reason": " "}],
                        [{**good, "points": -1}], None):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                recognition.validate_grade({**answer(choices), "deductions": entries}, TRANSCRIPT, rubric(), 10, 1, choices)

    def test_a_deduction_one_grader_took_off_and_the_other_did_not_is_a_disagreement(self):
        choices = grader_deductions.offered(rubric(), FROZEN)
        took = recognition.validate_grade(answer(choices), TRANSCRIPT, rubric(), 10, 1, choices)
        left = recognition.validate_grade(answer(choices, applied=False), TRANSCRIPT, rubric(), 10, 1, choices)
        self.assertTrue(risk.compare(took, took)["agrees"])
        compared = risk.compare(took, left)
        self.assertEqual((compared["agrees"], compared["disagreements"]), (False, ["deduction on R1"]))
        plain = recognition.validate_grade(answer(choices), TRANSCRIPT, rubric(), 10, 1)
        self.assertEqual(risk.compare(plain, plain)["disagreements"], [])

    def test_the_output_contract_asks_for_the_decisions_only_when_some_are_offered(self):
        choices = grader_deductions.offered(rubric(), FROZEN)
        payload = {"rubric": rubric()}
        for name in ("primary_vision", "second_vision", "transcript_grade", "grade"):
            with self.subTest(name=name):
                plain = grading_schema(name, payload, 1)
                self.assertNotIn("deductions", plain["properties"])
                asked = grading_schema(name, {**payload, "deductions": choices}, 1)
                self.assertIn("deductions", asked["required"])
                self.assertEqual(asked["properties"]["deductions"]["items"]["properties"]["id"], {"enum": [choices[0]["id"]]})
        self.assertNotIn("deductions", grading_schema("arbitrate", {**payload, "deductions": choices, "assessments": {}}, 1)["properties"])

    def test_reading_the_full_credit_line_again_keeps_the_grades_deduction(self):
        graded = {"applications": [{"rubric_item_id": "R1", "applied": True}], "score": score(rubric(), [{"rubric_item_id": "R1", "applied": True}], 10,
                                                                                                  [{"points": -.5, "reason": "R1: 6.0"}])}
        self.assertEqual(rubric_quality.adjusted(graded), [{"points": -.5, "reason": "R1: 6.0"}])
        self.assertEqual(rubric_quality.adjusted({"score": None}), ())

    def test_every_cloud_batch_freezes_the_rule_and_names_it_in_its_scope(self):
        settings = {}
        standard.freeze(settings)
        self.assertEqual((settings["grader_deductions_rule"], settings["settled_notes_rule"]), (grader_deductions.RULE, arbitration.SETTLED_RULE))
        self.assertTrue(grader_deductions.enabled(settings) and arbitration.settled_notes(settings))


class GraderPrompts(unittest.TestCase):
    """grade v8, transcript_grade v7, second_vision v5 and primary_vision v2: the earlier version word for word and three paragraphs."""
    def test_each_new_version_is_the_last_one_and_three_paragraphs(self):
        for name, version in (("grade", 8), ("transcript_grade", 7), ("second_vision", 5), ("primary_vision", 2)):
            with self.subTest(name=name):
                self.assertEqual(VERSIONS[name], version)
                old, new = ((PROMPTS / f"{name}-v{n}.txt").read_text() for n in (version - 1, version))
                self.assertEqual(new.count(",deductions:[{id,applied,reason}]"), 1)  # the answer's one new field
                # Apart from that field and the three paragraphs at the end, the contract is the old one word for word.
                head, tail = new.replace(",deductions:[{id,applied,reason}]", "").split("\n\nAPPROVED DEDUCTIONS")
                self.assertEqual(head, old.rstrip("\n"))
                added = ("APPROVED DEDUCTIONS" + tail).strip().split("\n\n")
                self.assertEqual([p.split(".")[0] for p in added], ["APPROVED DEDUCTIONS", "PRESENTATION THE RUBRIC DOES NOT NAME", "AN EMPTY ANSWER"])
                for said in ("P for the printed p", "$10x", "thousands separators", "a comma where ∪ joins intervals", "never take it off yourself"):
                    self.assertIn(said, new)


class SettledNotes(unittest.TestCase):
    ASKED = "Coordinator deduction: take off 0.25 point for an inequality in the objective."
    DOUBT = "The rubric does not say whether an uppercase P counts."

    def record(self, *texts, resolved=True):
        return {"rule": "flag-resolution-v2", "flags": [{"id": f"F{n}", "text": t, "resolved": resolved} for n, t in enumerate(texts, 1)]}

    def output(self, *blocks):
        return {"signals": {"risk": {"blocks": list(blocks), "individual": True}}, "reasons": [], "score": {"score": 1}, "state": "grading_review"}

    def test_the_name_of_who_asked_is_settled_with_every_word_behind_it(self):
        resolved = {self.ASKED, risk.SECOND_ASKED}
        proposal = {"review_reasons": [self.ASKED], "rubric_ambiguities": []}
        self.assertEqual(arbitration.renamed_notes([risk.GRADER_ASKED], resolved, proposal), [])
        # A request the ruling never saw keeps it, and so does a grade with nothing behind the sentence or no proposal at all.
        more = {"review_reasons": [self.ASKED, "Something new."], "rubric_ambiguities": []}
        self.assertEqual(arbitration.renamed_notes([risk.GRADER_ASKED], resolved, more), [risk.GRADER_ASKED])
        self.assertEqual(arbitration.renamed_notes([risk.GRADER_ASKED], resolved, {"review_reasons": []}), [risk.GRADER_ASKED])
        self.assertEqual(arbitration.renamed_notes([risk.GRADER_ASKED], resolved, None), [risk.GRADER_ASKED])
        # The wording sentence the same way, never one only the instructor settles, and never anything else.
        doubts = {"review_reasons": [], "rubric_ambiguities": [self.DOUBT]}
        self.assertEqual(arbitration.renamed_notes([risk.WORDING, risk.HANDWRITING], {self.DOUBT}, doubts), [risk.HANDWRITING])
        self.assertEqual(arbitration.renamed_notes([risk.WORDING], {self.DOUBT}, doubts, risk.instructor_only), [risk.WORDING])

    def test_a_flag_review_that_settled_the_second_graders_request_finalizes_its_grade(self):
        record = self.record("second: " + self.ASKED, risk.SECOND_ASKED)
        proposal = {"review_reasons": [self.ASKED], "rubric_ambiguities": []}
        settled = flag_resolution.apply(self.output(risk.GRADER_ASKED), record, proposal)
        self.assertEqual((settled["state"], settled["signals"]["risk"]["blocks"]), ("proposed", []))
        held = flag_resolution.apply(self.output(risk.GRADER_ASKED), record)  # a batch without the rule finishes as it ran
        self.assertEqual((held["state"], held["signals"]["flag_resolution"]["open_flags"]), ("grading_review", [risk.GRADER_ASKED]))
        unsettled = flag_resolution.apply(self.output(risk.GRADER_ASKED), self.record("second: " + self.ASKED, resolved=False), proposal)
        self.assertEqual(unsettled["state"], "grading_review")

    def test_the_arbitrators_adoption_settles_it_too_but_never_the_wording(self):
        record = {"outcome": "adopted", "rule": arbitration.RULE, "cloud_standard_rule": standard.RULE, "flags": self.record("second: " + self.ASKED, risk.SECOND_ASKED)["flags"]}
        settled = arbitration.apply(self.output(risk.GRADER_ASKED), record, {"review_reasons": [self.ASKED], "rubric_ambiguities": []})
        self.assertEqual(settled["state"], "proposed")
        wording = {**record, "flags": self.record(self.DOUBT)["flags"]}
        held = arbitration.apply(self.output(risk.WORDING), wording, {"review_reasons": [], "rubric_ambiguities": [self.DOUBT]})
        self.assertEqual(held["state"], "grading_review")  # rubric wording stays the instructor's beside the arbitrator

    def test_a_review_queued_now_carries_the_rule_to_its_carrier_batch(self):
        self.assertEqual(flag_resolution.scope({"flag_resolution_rule": flag_resolution.RULE, "settled_notes_rule": arbitration.SETTLED_RULE})["settled_notes_rule"],
                         arbitration.SETTLED_RULE)
        self.assertNotIn("settled_notes_rule", flag_resolution.scope({"flag_resolution_rule": flag_resolution.RULE}))


class EmptyAnswerRequests(unittest.TestCase):
    ASK = "R4 is supported by the blank area, but there is no transcript line to quote."

    def output(self, blocks, state="both", reading_issue=False):
        blank = risk.blank_reason(1)
        opinion = {"review_reasons": [self.ASK], **({"reading_issues": [{"description": "Faint marks?"}]} if reading_issue else {})}
        return {"state": state, "score": {"score": 0}, "transcript": {"lines": []}, "reasons": [blank],
                "signals": {"risk": {"blocks": [blank, *blocks], "individual": True,
                                     "components": {"reading": {"blank_crops": 1}, "committee": {"status": "resolved"}}},
                            "panels": [{"opinions": {"primary": {"review_reasons": []}, "second": opinion}}]}}

    def crops(self, registered=True):
        return {"crops": [{"metadata": {"registered": registered}}]}

    def test_a_graders_request_on_an_empty_answer_leaves_the_blank_to_jev(self):
        self.assertTrue(jev_blank.eligible(self.output([risk.SECOND_ASKED, "second: " + self.ASK]), self.crops()))
        self.assertTrue(jev_blank.eligible(self.output([risk.GRADER_ASKED, self.ASK]), self.crops()))
        # A reading issue, a rubric doubt or any other hold still keeps it, and "both" needs a request to explain it.
        for blocks in ([risk.SECOND_READING], [risk.SECOND_ASKED, risk.WORDING], ["second: Faint marks?"], [risk.HANDWRITING]):
            with self.subTest(blocks=blocks):
                self.assertFalse(jev_blank.eligible(self.output(blocks), self.crops()))
        self.assertFalse(jev_blank.eligible(self.output([]), self.crops()))
        self.assertTrue(jev_blank.eligible(self.output([], state="ocr_review"), self.crops()))

    def test_a_whole_page_area_is_shown_as_its_page_when_its_crop_did_not_align(self):
        page = [{"region": {"x": .04, "y": .07, "width": .92, "height": .89}, "privacy_masks": []}]
        third = [{"region": {"x": .04, "y": .07, "width": .86, "height": .28}, "privacy_masks": []}]
        masked = [{**page[0], "privacy_masks": [[0, 0, .2, .1]]}]
        unaligned = self.output([risk.CROP_UNSURE], state="ocr_review")
        self.assertEqual(jev_blank.shown_whole(self.crops(False), page), [0])
        self.assertTrue(jev_blank.eligible(unaligned, self.crops(False), page))
        # AN UNALIGNED CROP (2026-11-06) of any other area reaches Jev too, shown as it was cut.
        for regions in (third, masked, ()):
            with self.subTest(regions=regions):
                self.assertTrue(jev_blank.eligible(unaligned, self.crops(False), regions))
                self.assertEqual(jev_blank.shown_whole(self.crops(False), regions), [])
        self.assertEqual(jev_blank.shown_whole(self.crops(True), page), [])  # an aligned crop is shown as the crop
        # The crop's hold is excused only where a crop did not align, and never beside another hold.
        self.assertFalse(jev_blank.eligible(unaligned, self.crops(True), third))
        self.assertFalse(jev_blank.eligible(self.output([risk.CROP_UNSURE, risk.HANDWRITING], state="ocr_review"), self.crops(False), third))


class ThroughTheTeam(standard_fixture.CloudStandard):
    """A Cloud batch over the fake host: the graders' deductions decide the grade, and a split goes to the arbitrator."""

    def deducting(self, primary=True, second=True):
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        items = [{**item, **({"notes": DEDUCTION} if item["id"] == "R1" else {})} for item in RULES["items"]]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Synthetic deduction', 'rules': {**RULES, 'items': items}})
        self.seen = []

        def script(name, body):
            payload = payload_of(body)
            if name in ("primary_vision", "transcript_grade"):
                self.seen.append((name, payload.get("deductions")))
                applied = primary if name == "primary_vision" else second
                return {**grade_answer(payload, ("R1",)), "deductions": [{"id": d["id"], "applied": applied, "reason": "Scripted."} for d in payload["deductions"]]}
            if name == "arbitrate":
                self.seen.append((name, payload.get("deductions")))
                return arbitrate_answer(payload, adopt="standard.primary")
        return self.grade(CORRECT, script=script)

    def test_two_graders_who_take_off_the_same_deduction_finalize_the_grade(self):
        response, names = self.deducting()
        self.assertEqual(names, ["reconcile", "primary_vision", "transcript_grade"])
        self.assertTrue(all(offered and offered[0]["points"] == -.5 for _, offered in self.seen))
        result = response["result"]
        self.assertEqual((result["state"], result["score"]["score"], result["score"]["adjustments"][0]["points"]), ("proposed", 9.5, -.5))
        self.assertEqual((response["acceptance"]["kind"], response["acceptance"]["basis"]), ("automatic", "team_consensus"))
        self.assertEqual(result["signals"]["scope"]["cloud_standard"]["grader_deductions_rule"], grader_deductions.RULE)

    def test_graders_who_differ_on_a_deduction_go_to_the_arbitrator(self):
        response, names = self.deducting(second=False)
        self.assertIn("arbitrate", names)
        self.assertIn("deduction on R1", json.dumps(response["result"]["signals"]["panels"][0]["decision"]["pair"]))
        self.assertEqual((response["result"]["score"]["score"], response["acceptance"]["basis"]), (9.5, "final_arbitrator"))


# The fixture's own cases run in their own file.
for _name in dir(standard_fixture.CloudStandard):
    if _name.startswith("test") and _name not in vars(ThroughTheTeam):
        setattr(ThroughTheTeam, _name, None)


if __name__ == "__main__":
    unittest.main()
