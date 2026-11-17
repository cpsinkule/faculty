"""What may finalize a grade, and what only the instructor settles (review fixes, 2026-09-22).

Each case was a way a grade could be finalized automatically against a documented rule: a blank answer under the narrow review
policy (direct-reading-v2), a flag reviewer upholding a grade only one model produced (third-vote-v3), a ruling on a flag only the
instructor settles, a model family voting for itself, an arbitrator that never saw the spare grader's assessment, and a ruling
spoiled by its own re-asked answer. The unit cases call the rules directly; the rest run real retained tasks against the local
fake host, with no live inference.
"""
import json
import unittest
from unittest.mock import patch

from grading import arbitration, committee, direct_read, risk, standard
from grading.models import plain_transcript
from rosters import connect
from fake_openai import arbitrate_answer, flag_review_answer, grade_answer, images_of, payload_of
import test_grading_risk as risk_tests
import test_grading_standard as standard_tests
from test_grading_cloud import endpoint
from test_grading_pipeline import CORRECT, Pipeline

NARROW = risk.settings({"review_policy": "disagreement_and_work"})


def direct_reading(blank=(0,)):
    """One direct-reading candidate (no independent OCR) that found nothing written on the listed crops."""
    transcript = plain_transcript("", confidence=None)
    return {"candidates": [{"provider": direct_read.PROVIDER, "transcript": transcript, **({"blank_crops": list(blank)} if blank else {})}],
            "consensus": {"agreement": None, "disagreements": []}}


def verdict_of(policy, *, reading=None, second="agree", committee_=None, answer=None, rejected=0, graded_=None):
    e = risk_tests.evidence(**({"reading": reading} if reading else {}), **({"answer": answer} if answer else {}))
    other = {"agree": e["second"], "differ": risk_tests.graded(applied=("R2",))}.get(second)
    parts = risk.components(e["reading"], e["verified"], graded_ or e["graded"], e["crops"], e["answer"], e["symbolic"], other, None,
                            roles=risk_tests.ROLES, rubric=risk_tests.RUBRIC, rejected=rejected, committee=committee_,
                            settings={"visual_threshold": .85, "direct_reading_rule": direct_read.RULE})
    return parts, risk.assess(parts, policy)


def output(blocks=(), reasons=(), committee_=None, score=0):
    return {"transcript": {}, "applications": [], "interpretation": {}, "score": {"score": score} if score is not None else None, "state": "grading_review" if blocks else "proposed",
            "reasons": list(reasons), "signals": {"risk": {"blocks": list(blocks), "individual": True}, "committee": committee_ or {"status": "resolved", "reason": "agreed"}}}


def ruling(texts, rule, resolved=True, **more):
    return {"rule": rule, "outcome": "adopted", "flags": [{"id": f"F{n}", "text": t, "about": ["standard.primary"], "resolved": resolved, "note": "checked"}
                                                          for n, t in enumerate(texts, 1)], **more}


class Rules(unittest.TestCase):
    def test_a_blank_crop_is_a_block_under_every_policy_and_the_narrow_policy_never_waives_it(self):
        for policy in (risk_tests.POLICY, NARROW):
            with self.subTest(policy=policy["review_policy"]):
                parts, verdict = verdict_of(policy, reading=direct_reading())
                self.assertEqual(parts["reading"]["blank_crops"], 1)
                self.assertIn(risk.blank_reason(1), verdict["blocks"])
                self.assertNotIn(risk.blank_reason(1), verdict["reasons"])  # The reader's own reason says it; a grading reason would too.
        # The words are the reader's own, so the card shows one reason and a ruling's flag is the same flag.
        self.assertEqual(risk.blank_reason(1), "The reader found no handwriting on this response’s area; check whether it was left blank.")
        self.assertEqual(risk.blank_reason(2), "The reader found no handwriting on 2 of this response’s areas; check whether it was left blank.")
        self.assertNotIn("blank_crops", verdict_of(NARROW)[0]["reading"])  # Nothing new on the record of an ordinary response.

    def test_the_narrow_policy_merges_what_it_waives_with_what_direct_reading_dropped(self):
        _, verdict = verdict_of(NARROW, reading=direct_reading(blank=()))
        waived = verdict["waived"]
        self.assertEqual(waived["direct_reading"]["blocks"], ["No independent OCR reading stands beside the vision reader."])
        self.assertEqual(waived["policy"], "disagreement_and_work")
        _, every = verdict_of(risk_tests.POLICY, reading=direct_reading(blank=()))
        self.assertEqual(every["waived"], {"direct_reading": waived["direct_reading"]})

    def test_the_narrow_policy_keeps_a_teamless_pairs_disagreement(self):
        """Legacy and local jobs have no committee: their two graders disagreeing IS the grading team not agreeing."""
        _, verdict = verdict_of(NARROW, second="differ")
        self.assertIn("The primary grader and the second grader disagree.", verdict["blocks"])
        self.assertTrue(any(r.startswith("The primary grader and the second grader disagree on R1, R2") for r in verdict["reasons"]))
        self.assertTrue(verdict["individual"])
        self.assertEqual(verdict_of(NARROW)[1]["blocks"], [])  # An agreeing pair holds nothing back, as before.
        # A team keeps its own reason, exactly as before: nothing new is added beside it.
        team = committee.decide({**risk_tests.graded(), "lineage": "qwen3"}, {**risk_tests.graded(applied=("R2",)), "lineage": "gpt-oss"})
        _, verdict = verdict_of(NARROW, second=None, committee_=team)
        self.assertEqual(verdict["blocks"], [team["reason"]])

    def test_the_policy_label_moved_with_the_rules(self):
        self.assertEqual(risk.POLICY, "risk-rules-v15")

    def test_flags_only_the_instructor_settles(self):
        from grading.review_rules import CONDITION_PREFIX, LEGACY_CONDITION
        for text in (risk.NO_SECOND, risk.NO_PRIMARY, risk.INCOMPLETE, risk.NOBODY_ELSE, "The second grader failed.", risk.ONE_LINEAGE,
                     risk.CONDITION, risk.WORDING, risk.blank_reason(3), LEGACY_CONDITION, CONDITION_PREFIX + "R2: only if boxed — check its application to this response."):
            self.assertTrue(risk.instructor_only(text), text)
        for text in ("The grader’s evidence was incomplete or it asked for review.", risk.WORK_BLOCK, "Check whether the final digit is clear."):
            self.assertFalse(risk.instructor_only(text), text)
        # The committee's reasons for an incomplete pair are those very flags.
        self.assertEqual(committee.decide({**risk_tests.graded(), "lineage": "a"}, None)["reason"], risk.NO_SECOND)
        self.assertEqual(committee.decide({**risk_tests.graded(score=None), "lineage": "a"}, {**risk_tests.graded(), "lineage": "b"})["reason"], risk.NO_PRIMARY)

    def test_an_ordinary_disagreement_is_not_called_a_family_problem(self):
        split = committee.decide({**risk_tests.graded(), "lineage": "alpha"}, {**risk_tests.graded(applied=("R2",)), "lineage": "beta"})
        self.assertEqual(split["reason"], "The primary and secondary graders differ on R1, R2; review this response individually.")
        family = committee.decide({**risk_tests.graded(), "lineage": "alpha"}, {**risk_tests.graded(), "lineage": "Alpha"})
        self.assertIn("two model families", family["reason"])  # A batch frozen before the family rule.
        blind = committee.decide({**risk_tests.graded(), "lineage": "alpha"}, {**risk_tests.graded(), "lineage": "Alpha"}, families=False)
        self.assertEqual(blind["status"], "resolved")

    def test_a_third_assessment_can_support_a_shared_or_unknown_family(self):
        for family in ("gpt", ""):
            primary = {**risk_tests.graded(), "lineage": family}
            second = {**risk_tests.graded(applied=("R2",)), "lineage": family}
            decision = committee.decide(primary, second, primary.copy(), required=True, families=False)
            self.assertEqual((decision["status"], decision["source"]), ("resolved", "primary"))

    def test_a_retained_family_rejection_is_not_rewritten(self):
        record = {"rule": arbitration.RULE, "outcome": "unsettled", "same_family": True,
                  "reason": "Historic family restriction.", "flags": []}
        original = output(score=4)
        reviewed = arbitration.apply(original, record)
        self.assertEqual(reviewed["signals"]["arbiter"], record)
        self.assertEqual(reviewed["score"], original["score"])
        self.assertEqual(reviewed["state"], "grading_review")
        self.assertNotIn("arbiter", original["signals"])

    def test_a_cloud_standard_ruling_never_settles_an_instructor_only_flag(self):
        for text in (risk.WORDING, risk.CONDITION, risk.blank_reason(1), risk.NOBODY_ELSE):
            with self.subTest(flag=text):
                held = arbitration.apply(output([text]), ruling([text], arbitration.RULE, cloud_standard_rule=standard.RULE))
                self.assertEqual((held["state"], held["signals"]["arbiter"]["outcome"]), ("grading_review", "unsettled"))
                self.assertEqual(held["signals"]["arbiter"]["instructor_only"], [text])
                upheld = standard.apply(output([text]), {**ruling([text], standard.RULE), "outcome": "finalized", "reason": "fine"})
                self.assertEqual((upheld["state"], upheld["signals"]["flag_review"]["outcome"]), ("grading_review", "unsettled"))
        # Ordinary flags are still the ruling's to settle.
        settled = arbitration.apply(output(["Check whether the final digit is clear."]), ruling(["Check whether the final digit is clear."], arbitration.RULE, cloud_standard_rule=standard.RULE))
        self.assertEqual((settled["state"], settled["signals"]["arbiter"]["outcome"]), ("proposed", "adopted"))
        # A historical final-arbitrator batch keeps the rulings it ran with, except over a blank crop: direct-reading-v2 keeps that
        # out of automatic finalization in every batch it reads for.
        historical = arbitration.apply(output([risk.WORDING]), ruling([risk.WORDING], arbitration.RULE))
        self.assertEqual((historical["state"], historical["signals"]["arbiter"]["outcome"]), ("proposed", "adopted"))
        blank = arbitration.apply(output([risk.blank_reason(1)]), ruling([risk.blank_reason(1)], arbitration.RULE))
        self.assertEqual((blank["state"], blank["signals"]["arbiter"]["instructor_only"]), ("grading_review", [risk.blank_reason(1)]))

    def test_the_flag_reviewers_wording_reaches_the_committee_reason_too(self):
        text = "Check whether the final digit is clear."
        held = standard.apply(output([text, "Another doubt."]), {**ruling([text], standard.RULE), "outcome": "finalized", "reason": "fine"})
        self.assertEqual(held["signals"]["flag_review"]["outcome"], "unsettled")
        said = [held["signals"]["committee"]["reason"], *held["reasons"], *held["signals"]["risk"]["blocks"]]
        self.assertFalse([r for r in said if "final arbitrator" in r], said)
        self.assertTrue(held["signals"]["committee"]["reason"].startswith("The standing grade raised or left a flag"))

    def test_the_flag_reviewer_is_barred_where_no_model_may_finalize(self):
        a, b = {**risk_tests.graded(), "lineage": "alpha"}, {**risk_tests.graded(), "lineage": "beta"}
        agreed = committee.decide(a, b)
        doubt = [{"id": "F1", "text": "Check whether the final digit is clear."}]
        self.assertEqual(standard.barred(agreed, "standard.primary", a, doubt, {"lineage": "delta"}), "")
        self.assertIn("never compared", standard.barred(committee.decide(a, None), "standard.primary", a, doubt, {"lineage": "delta"}))
        # Before the family rule an agreement inside one family, and a reviewer of the standing grade's family, barred the reviewer.
        self.assertIn("share a model family", standard.barred(committee.decide(a, {**b, "lineage": "ALPHA"}), "standard.primary", a, doubt, {"lineage": "delta"}))
        self.assertIn("model family that produced", standard.barred(agreed, "standard.primary", a, doubt, {"lineage": "Alpha"}))
        self.assertEqual(standard.barred(committee.decide(a, {**b, "lineage": "ALPHA"}, families=False), "standard.primary", a, doubt, {"lineage": "delta"}, False), "")
        self.assertIn("none of the retained", standard.barred(agreed, "", a, doubt, {"lineage": "delta"}))
        self.assertEqual(standard.barred(agreed, "standard.primary", a, doubt, {"lineage": "Alpha"}, False), "")
        self.assertEqual(standard.barred(agreed, "standard.primary", a, doubt, {}, False), "")
        self.assertIn("Only the instructor", standard.barred(agreed, "standard.primary", a, [*doubt, {"id": "F2", "text": risk.WORDING}], {"lineage": "delta"}))

    def test_a_check_or_ruling_retained_before_the_bars_still_meets_them(self):
        """A batch in flight when this changed: its retained check never reaches the reviewer, and a retained ruling cannot finalize."""
        from unittest.mock import patch
        a, b = {**risk_tests.graded(), "lineage": "alpha"}, {**risk_tests.graded(), "lineage": "beta"}
        doubt = [{"id": "F1", "text": "Check whether the final digit is clear.", "about": ["standard.primary"]}]
        def run(existing, decision, settings={"family_rule": standard.FAMILY_RULE}):
            def ask(*args, **kwargs):
                raise AssertionError("Nothing may be sent.")
            with patch.object(standard.config, "roles", return_value={"flag_reviewer": {"name": "delta-vision", "lineage": "delta", "modality": "vision"}}):
                return standard.run(None, None, None, settings, existing, None, None, None, None, None, [], ask, {}, [], decision, a, {}, "cloud_standard")
        check = {"needed": True, "standing": "standard.primary", "flags": doubt}
        kind, proposal, opinions = run({standard.CHECK: check}, committee.decide(a, None))
        self.assertEqual((kind, proposal, opinions["flag_review"]["outcome"]), ("final", a, "not_asked"))
        upheld = {"decision": "finalize", "reason": "fine", "rulings": {"F1": {"resolved": True, "note": "checked"}}, "model": "fixture/alpha-review", "lineage": "alpha"}
        kind, proposal, opinions = run({standard.CHECK: check, standard.STEP: upheld}, committee.decide(a, b))
        self.assertEqual(opinions["flag_review"]["outcome"], "finalized")
        kind, proposal, opinions = run({standard.CHECK: check, standard.STEP: upheld}, committee.decide(a, b), {})  # Frozen before the family rule.
        self.assertEqual(opinions["flag_review"]["outcome"], "unsettled")
        self.assertIn("family upholding its own grade", opinions["flag_review"]["reason"])
        kind, proposal, opinions = run({standard.CHECK: check, standard.STEP: {**upheld, "lineage": "delta"}}, committee.decide(a, b))
        self.assertEqual(opinions["flag_review"]["outcome"], "finalized")  # Another family, an ordinary flag: its ruling stands.

    def test_a_spare_grader_has_its_own_label_and_the_standing_grade_is_found(self):
        spare = {**grade_answer({"transcript": {"lines": []}, "answer_parts": ["x"], "rubric": {"items": [{"id": "R1"}]}}, ("R1",)), "score": {"score": 10}, "lineage": "spare"}
        second = {**spare, "applications": [{**spare["applications"][0], "applied": False}], "lineage": "beta"}
        existing = {"grade:primary": {"failed": True, "error": "unavailable"}, "grade:primary:fallback": spare, "grade:second": second}
        labels = arbitration.candidates(existing)
        self.assertEqual(labels, {"standard.primary_fallback": "grade:primary:fallback", "standard.second": "grade:second"})
        self.assertEqual(arbitration.label_of(existing, spare, labels), "standard.primary_fallback")
        self.assertEqual(arbitration.label_of(existing, json.loads(json.dumps(spare)), labels), "standard.primary_fallback")  # An equal copy.
        self.assertEqual(arbitration.label_of(existing, {**spare, "lineage": "other"}, labels), "")  # Never a guess.


class Decisions(Pipeline):
    """Cloud Standard end to end: the fixture of `test_grading_standard` without its test cases."""
    current_standard, legacy_workflow = True, False
    opinions, scripted = standard_tests.CloudStandard.opinions, standard_tests.CloudStandard.scripted

    def setUp(self):
        super().setUp()  # Cloud Standard's own fixture: gamma-vision arbitrates, delta-vision reviews flags.
        self.change(endpoints=[endpoint(self.host, 'delta-vision')], standard={'verifier': 'gamma-vision', 'flag_reviewer': 'delta-vision'})

    def change(self, **values):
        saved = self.frq.settings()
        s = saved['settings']
        for key, value in values.items():
            if key == 'endpoints':
                s['endpoints'].extend(value)
            elif key == 'standard':
                s['tiers']['cloud_standard'].update(value)
            else:
                s[key] = value
        self.frq.save_settings({'version': saved['version'], 'settings': s})

    def steps(self, response):
        with connect(self.path) as db:
            return {r[0]: json.loads(r[1]) for r in db.execute("SELECT stage, output_json FROM frq_steps WHERE task_id=? ORDER BY rowid", (response['task']['id'],))}

    def test_shared_or_unknown_families_allow_clean_consensus(self):
        for family in ("gpt", ""):
            with self.subTest(family=family):
                saved = self.frq.settings()
                for connection in saved['settings']['endpoints']:
                    connection['lineage'] = family
                self.frq.save_settings(saved)
                response, names = self.scripted()
                self.assertEqual(response['acceptance']['basis'], 'team_consensus')
                self.assertEqual(response['result']['signals']['risk']['components']['votes']['count'], 2)
                self.assertEqual(response['result']['signals']['scope']['review_policy']['risk'], risk.POLICY)
                self.assertNotIn('arbitrate', names)
                self.assertNotIn('flag_review', names)
                self.assertEqual(response['result']['signals']['models']['primary']['lineage'], family)
                self.host.requests.clear()

    def test_a_blank_answer_is_never_finalized_under_the_narrow_policy(self):
        """The reader finds nothing, both graders tick the no-answer line: before the fix, a 0 finalized as team consensus."""
        self.change(direct_reading=True, glm_enabled=False, mathpix_enabled=False, risk={**self.frq.settings()['settings']['risk'], 'review_policy': 'disagreement_and_work'})
        def script(name, body):
            if name == 'read':
                return ('raw', '   ', 'stop')
            if name in ('grade', 'primary_vision', 'transcript_grade', 'second_vision'):
                return grade_answer(payload_of(body), ('R4',), image=bool(images_of(body)))
            if name == 'flag_review':
                return flag_review_answer(payload_of(body), 'finalize', True)
        response, names = self.grade(CORRECT, script=script)
        result = response['result']
        self.assertEqual(result['signals']['committee']['status'], 'resolved')  # The team agreed on the no-answer line.
        self.assertIsNone(response['acceptance'])
        self.assertEqual(result['state'], 'ocr_review')
        self.assertEqual(result['signals']['risk']['blocks'], [risk.blank_reason(1)])
        self.assertEqual([r for r in result['reasons'] if 'no handwriting' in r], [risk.blank_reason(1)])  # One reason, not two.
        self.assertNotIn(risk.blank_reason(1), result['signals']['risk']['waived']['review_state_reasons'])
        self.assertIn('direct_reading', result['signals']['risk']['waived'])  # Kept beside what the policy waived.
        # The flag reviewer could not have cleared it, so it was not asked, and the check step says why.
        self.assertNotIn('flag_review', names)
        self.assertEqual(result['signals']['flag_review']['outcome'], 'not_asked')
        self.assertIn('Only the instructor can settle', self.steps(response)[standard.CHECK]['not_asked'])

    def test_a_grade_only_one_model_produced_is_never_upheld_by_the_flag_reviewer(self):
        """third-vote-v3: a pair that never compared stays with the instructor, even when the reviewer would say finalize."""
        response, names = self.scripted(team={'alpha-vision': ('R1',), 'beta-text': lambda *_: {'invalid': True}},
                                        flags=lambda p: flag_review_answer(p, 'finalize', True))
        result = response['result']
        self.assertEqual(result['signals']['committee']['status'], 'unresolved')
        self.assertIsNone(response['acceptance'])
        self.assertNotIn('flag_review', names)
        self.assertNotIn('arbitrate', names)
        record = result['signals']['flag_review']
        self.assertEqual(record['outcome'], 'not_asked')
        self.assertIn('never compared two complete assessments', record['reason'])
        self.assertIn(risk.NO_SECOND, result['signals']['risk']['blocks'])

    def frozen_before_the_family_rule(self):
        """A batch frozen before `standard.FAMILY_RULE` (requirement, 2026-09-23) finishes under the family rules it ran with."""
        original = standard.freeze
        def freeze(settings):
            original(settings)
            settings.pop('family_rule')
        return patch('grading.standard.freeze', side_effect=freeze)

    def test_a_flag_reviewer_of_the_standing_graders_family_may_uphold_it(self):
        self.change(endpoints=[endpoint(self.host, 'alpha-review')], standard={'flag_reviewer': 'alpha-review'})
        response, names = self.scripted(review_reasons=['Check whether the final digit is clear.'], flags=lambda p: flag_review_answer(p, 'finalize', True))
        self.assertEqual(names[-1], 'flag_review')
        self.assertEqual((response['result']['signals']['flag_review']['outcome'], response['acceptance']['basis']), ('finalized', 'flag_review'))
        self.assertEqual(response['result']['signals']['scope']['cloud_standard']['family_rule'], standard.FAMILY_RULE)

    def test_before_the_family_rule_a_flag_reviewer_of_the_standing_graders_family_could_not_uphold_it(self):
        self.change(endpoints=[endpoint(self.host, 'alpha-review')], standard={'flag_reviewer': 'alpha-review'})
        with self.frozen_before_the_family_rule():
            response, names = self.scripted(review_reasons=['Check whether the final digit is clear.'], flags=lambda p: flag_review_answer(p, 'finalize', True))
        self.assertNotIn('flag_review', names)
        self.assertIsNone(response['acceptance'])
        self.assertIn('family upholding its own grade', response['result']['signals']['flag_review']['reason'])
        self.assertNotIn('family_rule', response['result']['signals']['scope']['cloud_standard'])

    def test_an_arbitrator_may_adopt_its_own_familys_assessment(self):
        self.change(endpoints=[endpoint(self.host, 'beta-vision')], standard={'verifier': 'beta-vision'})
        response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, adopt='standard.second'))
        arbiter = response['result']['signals']['arbiter']
        self.assertEqual((arbiter['outcome'], arbiter.get('same_family')), ('adopted', None))
        self.assertEqual((self.proposal(response), response['acceptance']['basis']), (['R1'], 'final_arbitrator'))

    def test_before_the_family_rule_an_arbitrator_adopting_its_own_familys_assessment_was_one_vote(self):
        self.change(endpoints=[endpoint(self.host, 'beta-vision')], standard={'verifier': 'beta-vision'})
        with self.frozen_before_the_family_rule():
            response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, adopt='standard.second'))
        arbiter = response['result']['signals']['arbiter']
        self.assertEqual((arbiter['outcome'], arbiter.get('same_family')), ('unsettled', True))
        self.assertIsNone(response['acceptance'])

    def test_two_graders_of_one_family_agreeing_is_an_agreement(self):
        self.change(endpoints=[endpoint(self.host, 'alpha-text', modality='text')], standard={'second': 'alpha-text'})
        response, names = self.scripted()
        self.assertEqual(response['result']['signals']['committee']['status'], 'resolved')
        self.assertEqual(response['acceptance']['basis'], 'team_consensus')
        self.assertNotIn(risk.ONE_LINEAGE, response['result']['signals']['risk']['blocks'])

    def test_before_the_family_rule_two_graders_of_one_family_were_one_vote(self):
        self.change(endpoints=[endpoint(self.host, 'alpha-text', modality='text')], standard={'second': 'alpha-text'})
        with self.frozen_before_the_family_rule():
            response, names = self.scripted()
        self.assertEqual(response['result']['signals']['committee']['status'], 'unresolved')
        self.assertTrue(any('two model families' in block for block in response['result']['signals']['risk']['blocks']))
        self.assertIsNone(response['acceptance'])

    def test_the_arbitrator_sees_the_spare_grader_that_stood_in(self):
        self.change(endpoints=[endpoint(self.host, 'spare-vision')], role_fallbacks={'cloud_standard': {'primary': 'spare-vision'}})
        def script(name, body):
            if name in ('arbitrate', 'flag_review'):
                self.seen[json.dumps(body, sort_keys=True)] = (name, payload_of(body))  # Consulted twice per request.
                return arbitrate_answer(payload_of(body), adopt='standard.second')
            if name in ('grade', 'primary_vision', 'transcript_grade', 'second_vision'):
                if body['model'] == 'fixture/alpha-vision':
                    return ('status', 500, {}, {'error': 'unavailable'})
                chosen = ('R2',) if body['model'] == 'fixture/spare-vision' else ('R1',)
                return grade_answer(payload_of(body), chosen, image=bool(images_of(body)))
        self.seen = {}
        response, names = self.grade(CORRECT, script=script)
        (_, shown), = self.seen.values()
        self.assertEqual(sorted(shown['assessments']), ['standard.primary_fallback', 'standard.second'])
        self.assertEqual(shown['standing'], 'standard.primary_fallback')
        self.assertEqual(self.steps(response)[arbitration.CHECK]['standing'], 'standard.primary_fallback')
        self.assertEqual((self.proposal(response), response['acceptance']['basis']), (['R1'], 'final_arbitrator'))

    def test_a_rulings_own_reasked_answer_is_not_a_new_flag(self):
        """The arbitrator's first answer breaks its contract and is asked again: that receipt must not unsettle its own ruling."""
        # The fixture consults the script twice per request; the re-asked request differs (it samples), so it is a new key.
        def script(name, body):
            if name == 'arbitrate':
                key = json.dumps(body, sort_keys=True)
                if key not in requests:
                    requests.append(key)
                return {'decision': 'maybe'} if requests.index(key) == 0 else arbitrate_answer(payload_of(body), adopt='standard.second')
            return self.opinions(**{'alpha-vision': ('R2',), 'beta-text': ('R1',)})(name, body)
        requests = []
        response, names = self.grade(CORRECT, script=script)
        self.assertGreaterEqual(names.count('arbitrate'), 2)
        with connect(self.path) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT stage FROM frq_rejected_outputs")], ['grade:arbiter'])
        result = response['result']
        self.assertEqual(result['signals']['arbiter']['outcome'], 'adopted', result['signals']['arbiter'].get('open_flags'))
        self.assertEqual(response['acceptance']['basis'], 'final_arbitrator')
        # The team's own refused answers still count: only the ruling's own receipt is left out.
        self.assertNotIn('A model answer was malformed or truncated and had to be asked again.', result['signals']['risk'].get('arbitrated', {}).get('blocks', []))


del Pipeline  # Imported test cases are collected again by the loader; this file runs only its own.
