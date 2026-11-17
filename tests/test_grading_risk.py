"""The rule-based risk policy: component signals in, escalation and acceptance blocks out. It never grades."""
import copy
import unittest

from grading import risk, standard
from grading.models import plain_transcript

POLICY = risk.settings({})
ROLES = {"reader": {"lineage": "qwen3"}, "primary": {"lineage": "qwen3"}, "second": {"lineage": "gpt-oss"}, "verifier": {"lineage": "kimi"}}
RUBRIC = {"items": [{"id": "R1", "exceptions": ""}, {"id": "R2", "exceptions": ""}], "warnings": []}


def graded(applied=("R1",), confidence=.97, consistent="yes", attempts=1, score=10):
    return {"applications": [{"rubric_item_id": item, "applied": item in applied, "confidence": confidence, "ambiguity": None} for item in ("R1", "R2")],
            "interpretation": {"work_consistent": consistent, "attempts": attempts}, "confidence": confidence, "review_reasons": [],
            "score": {"score": score} if score is not None else None}


def evidence(**changes):
    """A clear, correct, well-read response with shown work, a key match and an agreeing second grader of another lineage."""
    transcript = plain_transcript("3x - 8 = 10\n3x = 18\nx = 6", confidence=.97)
    base = {"reading": {"candidates": [{"provider": "glm", "transcript": transcript}, {"provider": "mathpix", "transcript": transcript}],
                        "consensus": {"agreement": 1.0, "disagreements": []}},
            "verified": {"transcript": transcript, "visual_confidence": .97}, "graded": graded(), "crops": {"crops": [{"metadata": {"registered": True}}]},
            "answer": {"check": "match", "readers_agree": True, "work": "shown", "work_required": True, "key": True, "full_credit": True},
            "symbolic": {"result": "pass", "pass": 3, "fail": 0, "indeterminate": 0, "contradictions": []}, "second": graded(), "verifier": None}
    base.update(changes)
    return base


# A batch created today freezes the family rule (`standard.FAMILY_RULE`); `settings=None` is a batch frozen before it.
CURRENT = {"visual_threshold": .85, "family_rule": standard.FAMILY_RULE}


def assess(e, contradictions=(), rejected=0, policy=POLICY, roles=ROLES, settings=CURRENT):
    parts = risk.components(e["reading"], e["verified"], e["graded"], e["crops"], e["answer"], e["symbolic"], e["second"], e["verifier"],
                            roles=roles, rubric=RUBRIC, rejected=rejected, settings=settings)
    return parts, risk.assess(parts, policy, contradictions)


class RiskPolicy(unittest.TestCase):
    def test_clear_agreement_neither_escalates_nor_blocks(self):
        parts, verdict = assess(evidence())
        self.assertEqual((verdict["level"], verdict["escalate"], verdict["blocks"], verdict["reasons"]), ("low", [], [], []))
        self.assertEqual(parts["votes"], {"basis": "assessments", "lineages": ["gpt-oss", "qwen3"], "count": 2})
        legacy, _ = assess(evidence(), settings=None)
        self.assertEqual(legacy["votes"], {"lineages": ["gpt-oss", "qwen3"], "count": 2})
        self.assertEqual(verdict["policy"], risk.POLICY)

    def test_a_clear_handwritten_response_is_not_escalated_by_what_is_true_of_every_response(self):
        # Seen in the first live run, where 4 of 4 responses escalated: a reader's whole-result confidence is the chance an entire
        # handwritten block is perfect (low for almost any handwriting), and "this rubric has conditional rules" is said of every response.
        from grading.recognition import RUBRIC_EXCEPTIONS
        clean = {"provider": "mathpix", "transcript": plain_transcript("3x - 8 = 10\n3x = 18\nx = 6", confidence=.5), "confidence_rate": .9961}
        e = evidence(reading={"candidates": [clean], "consensus": {"agreement": None, "disagreements": []}}, graded={**graded(), "review_reasons": [RUBRIC_EXCEPTIONS]})
        parts, verdict = assess(e)
        self.assertEqual((verdict["escalate"], verdict["level"]), ([], "low"))
        self.assertEqual((parts["ocr"]["min_rate"], parts["ocr"]["min_confidence"], parts["primary"]["review_reasons"]), (.9961, .5, 0))
        # The per-symbol rate is what marks a poor reading: the lowest tenth of 78 real responses sat below .93, the median at .993.
        misread = {**clean, "confidence_rate": .9081}
        self.assertIn("A reader reported low confidence.", assess(evidence(reading={"candidates": [misread], "consensus": {"agreement": None, "disagreements": []}}))[1]["escalate"])
        self.assertEqual(assess(evidence(reading={"candidates": [misread], "consensus": {"agreement": None, "disagreements": []}}), policy=risk.settings({"min_ocr_rate": .9}))[1]["escalate"], [])
        # A reason about THIS response still escalates.
        self.assertIn("The grader’s evidence was incomplete or it asked for review.", assess(evidence(graded={**graded(), "review_reasons": [RUBRIC_EXCEPTIONS, "R1: no supported evidence; the selection requires instructor review."]}))[1]["escalate"])

    def test_the_instructors_switch_stops_reported_confidence_from_flagging_anything_and_nothing_else(self):
        """requirement, 2026-09-20: a toggle for the confidence threshold checks. Numbers a model reports about itself, never what it names."""
        from grading import recognition, review_rules
        off = risk.settings({"confidence_checks": False})
        self.assertTrue(POLICY["confidence_checks"])
        self.assertTrue(risk.confidence_checks({"min_confidence": .9}))  # A batch frozen before the switch existed keeps its checks.
        with self.assertRaises(ValueError):
            risk.settings({"confidence_checks": "no"})
        misread = {"provider": "mathpix", "transcript": plain_transcript("3x - 8 = 10\n3x = 18\nx = 6", confidence=.5), "confidence_rate": .62}
        doubtful = evidence(reading={"candidates": [misread], "consensus": {"agreement": None, "disagreements": []}},
                            verified={"transcript": plain_transcript("3x - 8 = 10\n3x = 18\nx = 6", confidence=.4), "visual_confidence": .4}, graded=graded(confidence=.3))
        flagged = assess(doubtful)[1]["escalate"]
        for reason in ("A reader reported low confidence.", "The handwriting is ambiguous in places.", "The grader was uncertain about a rubric decision."):
            self.assertIn(reason, flagged)
        parts, quiet = assess(doubtful, policy=off)
        self.assertEqual((quiet["escalate"], quiet["level"]), ([], "low"))
        self.assertEqual((parts["ocr"]["min_rate"], parts["reading"]["visual_confidence"], parts["primary"]["confidence"]), (.62, .4, .3))  # Still recorded.
        # What a model NAMES is not a confidence number: an ambiguity it wrote down still escalates with the switch off.
        named = plain_transcript("3x - 8 = 10\nx = 6", confidence=.97, ambiguities=["line-2: the 6 may be an 8"])
        self.assertIn("The handwriting is ambiguous in places.", assess(evidence(verified={"transcript": named, "visual_confidence": .97}), policy=off)[1]["escalate"])
        unsure = graded()
        unsure["applications"][0]["ambiguity"] = "The rubric line could apply either way."
        self.assertIn("The grader was uncertain about a rubric decision.", assess(evidence(graded=unsure), policy=off)[1]["escalate"])
        # The same switch in the review state: low numbers send nothing to review, a named ambiguity still does.
        settings = {"ocr_threshold": .85, "visual_threshold": .85, "grading_threshold": .9}
        grading = {**graded(confidence=.3), "review_reasons": [], "rubric_ambiguities": []}
        args = (doubtful["reading"], doubtful["verified"], grading, doubtful["crops"])
        state, _, reasons = recognition.review_state(*args, {**settings, "risk": POLICY})
        self.assertEqual((state, sorted(reasons)), ("both", sorted(["Visual reading needs confirmation.", "An OCR provider reported low confidence.", "The proposed rubric application needs review."])))
        self.assertEqual(recognition.review_state(*args, {**settings, "risk": off})[::2], ("proposed", []))
        self.assertEqual(recognition.review_state(doubtful["reading"], {"transcript": named, "visual_confidence": .4}, grading, doubtful["crops"], {**settings, "risk": off})[0], "ocr_review")
        self.assertFalse(recognition.low_ocr_confidence([misread], {**settings, "risk": off}))
        # Settling OCR differences no longer waits for a reported number either; the clear, consistent, registered reading decides.
        clean = plain_transcript("x = 6", confidence=.97)
        differing = {"candidates": [{"provider": "glm", "transcript": clean}, {"provider": "mathpix", "transcript": plain_transcript("x = 8", confidence=.97)}],
                     "consensus": {"agreement": .5, "disagreements": [{"providers": ["glm", "mathpix"], "values": ["x = 6", "x = 8"]}]}}
        verified, crops = {"transcript": clean, "visual_confidence": .4}, {"crops": [{"metadata": {"registered": True}}]}
        self.assertEqual(review_rules.reading_resolution(differing, verified, crops, {**settings, "risk": POLICY})["resolved"], 0)
        self.assertEqual(review_rules.reading_resolution(differing, verified, crops, {**settings, "risk": off})["resolved"], 1)

    def test_every_selection_disagreement_escalates_and_requires_individual_review_regardless_of_score(self):
        # A ceiling can hide extra credit; equal-point alternatives and zero-point choices still matter.
        for selected, total in ((("R1", "R2"), 10), (("R2",), 10), (("R1", "R2"), 4)):
            with self.subTest(selected=selected, total=total):
                e = evidence(second=graded(applied=selected, score=total))
                before = copy.deepcopy(e)
                parts, verdict = assess(e)
                self.assertEqual((parts["second"]["agrees"], parts["second"]["same_score"], parts["votes"]["count"]), (False, total == 10, 1))
                self.assertEqual((verdict["individual"], verdict["level"]), (True, "high"))
                self.assertIn("The primary grader and the second grader disagree.", verdict["escalate"])
                self.assertTrue(set(verdict['escalate']) <= set(verdict['blocks']))
                self.assertIn('review the rubric selections individually', verdict['reasons'][0])
                self.assertEqual(e, before)

    def test_an_initial_disagreement_survives_later_agreement(self):
        e = evidence()
        comparison = risk.compare(e['graded'], graded(applied=('R1', 'R2')))
        parts = risk.components(**e, roles=ROLES, escalation={'escalated': True, 'rubric_disagreement': comparison})
        verdict = risk.assess(parts, POLICY)
        self.assertTrue(parts['second']['agrees'])
        self.assertEqual((verdict['individual'], verdict['level']), (True, 'high'))
        self.assertIn('disagree on R2', verdict['reasons'][0])

    def test_missing_item_decisions_are_named_in_the_disagreement(self):
        other = graded()
        other['applications'].pop()
        self.assertEqual(risk.compare(graded(), other)['disagreements'], ['R2'])

    def test_unclear_rubric_wording_is_for_the_instructor_and_buys_no_more_compute(self):
        # "The rubric asks for 43/2 and the student wrote 21.5": another model cannot settle what the rubric leaves open.
        note = "R1 requires the slope to be written as 43/2, but the student wrote 21.5; the rubric does not say whether a decimal is accepted."
        parts, verdict = assess(evidence(graded={**graded(), "review_reasons": [note], "rubric_ambiguities": [note]}))
        self.assertEqual((parts["primary"]["review_reasons"], parts["primary"]["rubric_ambiguities"], verdict["escalate"]), (0, 1, []))
        self.assertIn("The grader found the rubric’s wording unclear for this response.", verdict["blocks"])

    def test_every_component_signal_is_persisted_not_only_the_verdict(self):
        parts, _ = assess(evidence())
        self.assertEqual(set(parts), {"ocr", "reading", "primary", "answer", "symbolic", "second", "verifier", "output", "rubric", "escalation", "votes"})
        self.assertEqual(parts["second"]["separate_assessment"], True)
        self.assertIsNone(parts["verifier"])  # Absent evidence is recorded as absent, never as agreement.

    def test_each_escalation_signal_of_the_goal(self):
        other = plain_transcript("3x - 8 = 10\n3x = 18\nx = 8", confidence=.97)
        cases = {
            "Independent readers disagree": evidence(reading={"candidates": [{"provider": "glm", "transcript": other}] * 2, "consensus": {"agreement": .9, "disagreements": [{}]}}),
            "A reader reported low confidence": evidence(reading={"candidates": [{"provider": "glm", "transcript": plain_transcript("x", confidence=.5)}], "consensus": {"agreement": None, "disagreements": []}}),
            "grader was uncertain": evidence(graded=graded(confidence=.6)),
            "second grader disagree": evidence(second=graded(applied=("R2",), score=4)),
            "symbolic check contradicts": evidence(symbolic={"result": "fail", "pass": 1, "fail": 2, "indeterminate": 0, "contradictions": ["“3x = 18” gives x = 6, but “x = 8” gives x = 8."]}),
            "correct final answer is inconsistent with the shown work": evidence(graded=graded(consistent="no")),
            "found the work inconsistent, yet full credit": evidence(graded=graded(consistent="no")),
            "several distinct solution attempts": evidence(graded=graded(attempts=2)),
            "cannot be combined under the approved rules": evidence(graded=graded(score=None)),
            "alignment is uncertain": evidence(crops={"crops": [{"metadata": {"registered": False}}]}),
        }
        for expected, case in cases.items():
            with self.subTest(signal=expected):
                _, verdict = assess(case)
                self.assertTrue(any(expected in reason for reason in verdict["escalate"]), verdict["escalate"])
                self.assertTrue(set(verdict["escalate"]) <= set(verdict["blocks"]))  # Whatever is worth more compute is never accepted unseen.
        _, malformed = assess(evidence(), rejected=1)
        self.assertTrue(any("malformed or truncated" in reason for reason in malformed["escalate"]))

    def test_crossed_out_work_is_recorded_without_escalation_or_review(self):
        clean = evidence()
        struck = copy.deepcopy(clean)
        abandoned = plain_transcript("x = 8", confidence=.97)["lines"][0]
        abandoned["id"] = "abandoned"
        abandoned["elements"][0]["state"] = "crossed_out"
        struck["verified"]["transcript"]["lines"].insert(-1, abandoned)
        parts, verdict = assess(struck)
        self.assertEqual(parts["reading"]["crossed_out"], 1)
        self.assertEqual(verdict, assess(clean)[1])

    def test_a_symbolic_contradiction_or_grader_disagreement_is_high_risk_with_a_reason_for_the_instructor(self):
        _, verdict = assess(evidence(symbolic={"result": "fail", "pass": 0, "fail": 1, "indeterminate": 0, "contradictions": ["18/3 is not 8."]}))
        self.assertEqual(verdict["level"], "high")
        self.assertEqual(verdict["reasons"], ["The written work is mathematically inconsistent, yet full credit was proposed: 18/3 is not 8."])
        _, verdict = assess(evidence(second=graded(applied=(), score=0)))
        self.assertEqual(verdict["level"], "high")
        self.assertIn("disagree on R1", verdict["reasons"][0])

    def test_shared_or_unknown_families_do_not_merge_assessments(self):
        for lineage in ("qwen3", "QWEN3", ""):
            with self.subTest(lineage=lineage):
                same = {**ROLES, "second": {"lineage": lineage}}
                parts, verdict = assess(evidence(), roles=same)
                self.assertEqual((parts["votes"]["count"], parts["second"]["separate_assessment"]), (2, True))
                self.assertEqual(verdict["blocks"], [])
                self.assertEqual(verdict["escalate"], [])

    def test_before_the_family_rule_models_of_one_lineage_are_one_vote(self):
        same = {**ROLES, "second": {"lineage": "qwen3"}}
        parts, verdict = assess(evidence(), roles=same, settings=None)
        self.assertEqual((parts["votes"]["count"], parts["second"]["independent"]), (1, False))
        self.assertTrue(any("shares the primary grader’s lineage" in block for block in verdict["blocks"]))
        self.assertEqual(verdict["escalate"], [])  # Not a reason to spend more compute; only never a second vote.

    def test_acceptance_is_blocked_without_independent_evidence(self):
        for expected, case in {
            "No independent second grader": evidence(second=None),
            "no confirmed answer key": evidence(answer={"check": "unknown", "readers_agree": True, "work": "shown", "work_required": True, "key": False, "full_credit": True}),
            "does not match a confirmed answer key": evidence(answer={"check": "mismatch", "readers_agree": True, "work": "shown", "work_required": True, "key": True, "full_credit": True}),
            # An answer the check could not read still blocks, but is not called a wrong answer (risk-rules-v14).
            "could not be compared with the confirmed answer key": evidence(answer={"check": "unknown", "readers_agree": True, "work": "shown", "work_required": True, "key": True, "full_credit": True}),
            "rests on one reading": evidence(answer={"check": "match", "readers_agree": False, "work": "shown", "work_required": True, "key": True}),
            "rubric requires supporting work and little work is visible": evidence(answer={"check": "match", "readers_agree": True, "work": "none", "work_required": True, "key": True, "full_credit": True}),
        }.items():
            with self.subTest(block=expected):
                self.assertTrue(any(expected in block for block in assess(case)[1]["blocks"]))
        # An unresolved team has no supporter, which is not "nobody looked": where a second assessment was compared, the team's own
        # reason is the block (25 of 25 unresolved responses of one batch carried the wrong sentence).
        e = evidence(second=None)
        team = {"status": "unresolved", "reason": "The additional reviewer does not agree with either independent assessment.", "pair": {"agrees": False, "disagreements": ["R1"]}}
        parts = risk.components(e["reading"], e["verified"], e["graded"], e["crops"], e["answer"], e["symbolic"], None, None, roles=ROLES, rubric=RUBRIC, committee=team)
        blocks = risk.assess(parts, POLICY)["blocks"]
        self.assertEqual(([b for b in blocks if "No independent second grader" in b], team["reason"] in blocks), ([], True))
        # No final answer was reported: there is nothing two readers could differ on, so the one-reading block does not apply.
        blank = evidence(answer={"check": "unknown", "readers_agree": None, "work": "none", "work_required": True, "key": True, "full_credit": False})
        self.assertFalse(any("rests on one reading" in block for block in assess(blank)[1]["blocks"]))
        contradicted = assess(evidence(), contradictions=["Full credit was proposed with little visible work."])[1]
        self.assertTrue(any("contradicts the answer facts" in reason for reason in contradicted["escalate"] + contradicted["blocks"]))
        self.assertEqual(contradicted["level"], "high")

    def test_answer_key_acceptance_requirement_only_applies_to_full_credit(self):
        for total in (0, 4, 10):
            for key, check in ((True, 'match'), (True, 'mismatch'), (True, 'unknown'), (False, 'unknown')):
                with self.subTest(score=total, key=key, check=check):
                    proposal = graded(applied=('R1',) if total == 10 else ('R2',) if total else (), score=total)
                    answer = {**evidence()['answer'], 'key': key, 'check': check, 'full_credit': total == 10}
                    case = evidence(answer=answer, graded=proposal, second=proposal)
                    verdict = assess(case)[1]
                    blocked = total == 10 and check != 'match'
                    self.assertEqual(bool(verdict['blocks']), blocked, verdict)
                    if blocked:
                        self.assertIn('Full credit was proposed', verdict['blocks'][0])
                    self.assertEqual(assess(case, policy=risk.settings({'require_key_match': False}))[1]['blocks'], [])

    def test_supporting_work_acceptance_requirement_only_applies_to_full_credit(self):
        for total in (0, 4, 10):
            for work in ('none', 'thin', 'shown'):
                for required in (False, True):
                    with self.subTest(score=total, work=work, required=required):
                        proposal = graded(applied=('R1',) if total == 10 else ('R2',) if total else (), score=total)
                        answer = {**evidence()['answer'], 'work': work, 'work_required': required, 'full_credit': total == 10}
                        case = evidence(answer=answer, graded=proposal, second=proposal)
                        verdict = assess(case)[1]
                        self.assertEqual(bool(verdict['blocks']), total == 10 and required and work != 'shown', verdict)
                        if verdict['blocks']:
                            self.assertIn('Full credit was proposed', verdict['blocks'][0])
                        self.assertEqual(assess(case, policy=risk.settings({'require_work_shown': False}))[1]['blocks'], [])

    def test_an_error_the_proposal_already_accounts_for_is_ordinary_grading_not_an_alarm(self):
        """Partial credit for work with an arithmetic slip: the symbolic failure is recorded, but nothing contradicts the proposal."""
        slip = evidence(graded=graded(applied=("R2",), consistent="no", score=4), second=graded(applied=("R2",), consistent="no", score=4),
                        symbolic={"result": "fail", "pass": 1, "fail": 1, "indeterminate": 0, "contradictions": ["“2x - 3 = 7” gives x = 5, but “2x = 4” gives x = 2."]},
                        answer={"check": "mismatch", "readers_agree": True, "work": "shown", "work_required": True, "key": True, "full_credit": False})
        parts, verdict = assess(slip)
        self.assertEqual((verdict["escalate"], verdict["reasons"], verdict["level"]), ([], [], "low"))
        self.assertEqual(parts["symbolic"]["fail"], 1)
        self.assertIn("The written work contains a mathematical error.", verdict["blocks"])  # Grouped for one look; never accepted unseen.

    def test_an_objection_is_evidence_that_blocks_acceptance_but_routes_only_once_its_precision_is_measured(self):
        objection = {"objections": [{"rubric_item_id": "R1", "claim": "The 6 is an 8.", "severity": "material"}], "no_material_contradiction": False, "transcript_disagreements": []}
        parts, verdict = assess(evidence(verifier=objection))
        self.assertEqual((parts["verifier"]["material"], parts["verifier"]["separate_assessment"]), (1, True))
        self.assertIn("The adversarial reviewer raised a material objection.", verdict["blocks"])
        self.assertEqual(verdict["reasons"], [])  # A model told to find an error usually reports one: it does not add review until measured.
        _, routed = assess(evidence(verifier=objection), policy=risk.settings({"verifier_routes": True}))
        self.assertEqual(len(routed["reasons"]), 1)
        clean = {"objections": [], "no_material_contradiction": True, "transcript_disagreements": []}
        self.assertEqual(assess(evidence(verifier=clean))[1]["blocks"], [])

    def test_thresholds_are_configurable_and_a_response_never_escalates_twice(self):
        strict = risk.settings({"min_confidence": .99})
        self.assertTrue(assess(evidence(), policy=strict)[1]["escalate"])
        with self.assertRaises(ValueError):
            risk.settings({"max_escalation_depth": 2})
        with self.assertRaises(ValueError):
            risk.settings({"unknown": 1})


if __name__ == "__main__":
    unittest.main()
