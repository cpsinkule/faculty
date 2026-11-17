"""End-to-end synthetic responses through the real job runner: reading, two graders of different lineages, symbolic checks and
the rule-based risk policy, against the local fake host. High Assurance, the escalation it offered and its adversarial reviewer were
removed on 2026-09-23: what would once have escalated is still recorded (`escalation.considered`), and nothing escalates.

The scripted models are deliberately FOOLED where a test says so: agreement among models that share an input cannot detect an
error in that input, so the deterministic evidence has to. Nothing here ever selects a rubric line or approves a response.
"""
import json
import unittest
import uuid
from unittest.mock import patch
from werkzeug.exceptions import Conflict

from grading import risk
from grading.models import plain_transcript
from grading.providers.local_vlm import VERSIONS
from rosters import connect
import test_grading_cloud as cloud_fixture
from fake_openai import adversarial_answer, grade_answer, payload_of, prompt_name, reconcile_answer

RULES = {"mode": "positive", "items": [
    {"id": "R1", "description": "Correct Answer: $$x=6$$ and work to justify answer.", "points": 10},
    {"id": "R2", "description": "Set up the equation correctly", "points": 4},
    {"id": "R3", "description": "Correct answer, but no work to justify answer.", "points": 2},
    {"id": "R4", "description": "No Answer or Completely Incorrect", "points": 0}]}
CORRECT = ["3x - 8 = 10", "3x = 10 + 8", "3x = 18", "x = 6"]
INCONSISTENT = ["3x - 8 = 10", "3x = 10 + 8", "3x = 18", "x = 8"]
SLIP = ["3x - 8 = 10", "3x = 10 - 8", "3x = 2", "x = 2/3"]
BARE = ["x = 6"]


class Reader:
    """GLM-OCR (the local GPU) as a fixture that reads whatever the test wrote."""
    def __init__(self, lines, confidence=.97):
        self.lines, self.confidence = lines, confidence

    def health(self):
        return {"status": "available"}

    def recognize(self, image):
        text = "\n".join(self.lines)
        return {"provider": "glm", "transcript": plain_transcript(text, confidence=self.confidence), "raw": {"text": text}, "metadata": {"model": "fixture-ocr"}}


class Pipeline(cloud_fixture.CloudCase):
    # These tests also cover resuming immutable pre-team jobs after an upgrade.
    # The current workflow is exercised separately in test_grading_teams.
    legacy_workflow = True

    def queue(self, *args, **kwargs):
        job = super().queue(*args, **kwargs)
        if self.legacy_workflow:
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile.pop('grading_workflow', None)
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
        return job

    def setUp(self):
        super().setUp()
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        q = self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "Coordinator rubric", "rules": RULES})["questions"][0]
        self.frq.approve_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        plan = self.frq.assignment(self.plan["id"])
        self.frq.save_answer_key(plan["id"], {"version": plan["version"], "questions": [{"question_id": self.qid, "version": plan["questions"][0]["version"],
            "answer_key": [{"label": "x", "accept": ["x=6"]}], "worked_solution": "3x = 18, so x = 6"}]})
        host = self.host
        endpoints = [cloud_fixture.endpoint(host, "alpha-vision"), cloud_fixture.endpoint(host, "beta-text", modality="text"), cloud_fixture.endpoint(host, "gamma-vision")]
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": endpoints, "yardstick": "", "max_retries": 3,
            "blind_share": 0.0,  # One trial acceptance in five is blind by default, which hides the decision these tests read; the blind share has its own tests.
            "tiers": {"cloud_standard": {"reader": "alpha-vision", "primary": "alpha-vision", "second": "beta-text", "verifier": ""}}}})

    def grade(self, lines, primary=("R1",), second=None, *, mode="cloud_standard", reader=None, objections=(), mathpix=None, script=None, **grader):
        """One response through the whole pipeline. Returns (response, requests by prompt name)."""
        second = primary if second is None else second
        def answer(body, host):
            name = prompt_name(body)
            if script and script(name, body) is not None:
                return script(name, body)
            if name in ("reconcile", "reconcile_literal", "reconcile_context"):
                return (reader or reconcile_answer)(payload_of(body))
            if name == "adversarial":
                return adversarial_answer(objections)
            first = name in ("grade", "primary_vision")  # A Cloud Standard batch asks its primary under its own vision contract.
            return grade_answer(payload_of(body), primary if first else second, image=name in ("grade", "primary_vision", "second_vision"), **(grader if first else {}))
        self.host.script = answer
        patch.stopall()
        from frq_fixtures import page
        patch("grading.extraction.extract", return_value={"metadata": {"registered": True, "crop_bounds": [0, 0, 100, 100]},
            "images": {k: page() for k in ("original", "grayscale", "contrast", "ink", "cloud")}}).start()
        patch.object(self.frq, "provider", return_value=Reader(lines)).start()
        if mathpix is not None:
            real = self.frq.cloud_provider
            class Mathpix:
                kind = "mathpix"
                def cost(self, usage):
                    return None, "billed separately"
                def submit(self, request):
                    text = "\n".join(mathpix)
                    return {"state": "completed", "result": {"output": {"provider": "mathpix", "transcript": plain_transcript(text, confidence=.95), "raw": {}, "metadata": {}}, "raw": {}, "metadata": {"usage": {}}}}
            patch.object(self.frq, "cloud_provider", side_effect=lambda e, timeout=180: Mathpix() if e.get("kind") == "mathpix" else real(e, timeout)).start()
            settings = self.frq.settings()
            self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "mathpix_enabled": True}})
        self.addCleanup(patch.stopall)
        job = self.queue(mode=mode)
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual(done["status"], "review_ready", done)
        names = [prompt_name(r["body"]) for r in self.host.requests if r["path"].endswith("/chat/completions")]
        return self.frq.response(done["tasks"][0]["response_id"]), names

    def proposal(self, response):
        return sorted(a["rubric_item_id"] for a in response["result"]["applications"] if a["applied"])


class SyntheticResponses(Pipeline):
    def test_a_clear_correct_response_takes_the_normal_path(self):
        response, names = self.grade(CORRECT)
        result, signals = response["result"], response["result"]["signals"]
        self.assertEqual((result["state"], result["reasons"], result["score"]["score"]), ("proposed", [], 10))
        self.assertEqual(names, ["reconcile", "grade", "transcript_grade"])
        self.assertEqual(signals["escalation"], {"escalated": False, "depth": 0, "from": "cloud_standard", "to": "cloud_standard", "reasons": [], "considered": [], "maximum_depth": 0})
        self.assertEqual((signals["risk"]["level"], signals["risk"]["blocks"], signals["risk"]["escalate"]), ("low", [], []))
        self.assertEqual(signals["risk"]["components"]["votes"], {"basis": "assessments", "lineages": ["alpha", "beta"], "count": 2})
        self.assertEqual((signals["answer"]["check"], signals["answer"]["work"], signals["second"]["lineage"]), ("match", "shown", "beta"))
        self.assertEqual({c["result"] for c in signals["symbolic"]["checks"]}, {"pass"})
        self.assertEqual(signals["models"]["primary"], {"endpoint": "alpha-vision", "model": "fixture/alpha-vision", "lineage": "alpha"})

    def test_the_inconsistent_response_is_found_inconsistent_even_when_every_model_is_fooled(self):
        """3x - 8 = 10, 3x = 18, x = 8. Both scripted graders propose full credit with high confidence; the mathematics does not."""
        response, names = self.grade(INCONSISTENT)
        result, signals = response["result"], response["result"]["signals"]
        self.assertNotEqual(result["state"], "proposed")  # It never passes through as a routine high-confidence grade.
        self.assertEqual(signals["risk"]["level"], "high")
        self.assertTrue(any("mathematically inconsistent" in r and "gives x = 6, but “x = 8” gives x = 8" in r for r in result["reasons"]), result["reasons"])
        failed = [c for c in signals["symbolic"]["checks"] if c["result"] == "fail"]
        self.assertEqual({c["type"] for c in failed}, {"equation_chain", "final_value"})  # 18/3 is not 8, and 3(8) - 8 is not 10.
        self.assertTrue(any("16 = 10" in c["explanation"] for c in failed))
        self.assertEqual(signals["answer"]["check"], "mismatch")
        # The policy would once have escalated it; it is recorded as considered, and nothing more is bought.
        self.assertEqual((signals["escalation"]["escalated"], bool(signals["escalation"]["considered"])), (False, True))
        self.assertEqual((names.count("grade"), names.count("adversarial")), (1, 0))
        self.assertEqual(self.proposal(response), ["R1"])  # The evidence flags the proposal; it never rewrites it.
        self.assertEqual(result["transcript"]["plain_text"].splitlines()[-1], "x = 8")  # And the written error is never repaired.

    def test_an_arithmetic_slip_with_partial_credit_is_ordinary_grading(self):
        response, names = self.grade(SLIP, primary=("R2",), consistent="no")
        result, signals = response["result"], response["result"]["signals"]
        self.assertEqual((result["state"], result["score"]["score"], self.proposal(response)), ("proposed", 4, ["R2"]))
        self.assertGreaterEqual(sum(c["result"] == "fail" for c in signals["symbolic"]["checks"]), 1)  # Found and retained ...
        self.assertFalse(signals["escalation"]["escalated"])  # ... but the proposal already accounts for it, so no alarm and no extra compute.
        self.assertIn("The written work contains a mathematical error.", signals["risk"]["blocks"])
        self.assertNotIn("adversarial", names)

    def test_a_correct_answer_with_unsupported_work_is_not_full_credit_material(self):
        response, names = self.grade(BARE)
        result, signals = response["result"], response["result"]["signals"]
        self.assertEqual((signals["answer"]["check"], signals["answer"]["work"]), ("match", "none"))
        self.assertIn("Full credit was proposed with little visible work; check that the work justifies the answer.", result["reasons"])
        self.assertNotEqual(result["state"], "proposed")
        self.assertEqual((signals["escalation"]["escalated"], bool(signals["escalation"]["considered"])), (False, True))
        self.assertEqual(self.proposal(response), ["R1"])  # Answer correctness and valid reasoning are told apart; the rubric line is the instructor's to change.
        # The rubric's own no-work line is consistent with the facts and passes the acceptance requirements.
        other, _ = self.grade(BARE, primary=("R3",))
        self.assertEqual((other["result"]["state"], other["result"]["score"]["score"], other["result"]["signals"]["escalation"]["escalated"]), ("proposed", 2, False))
        self.assertEqual(other["result"]["signals"]["risk"]["blocks"], [])
        self.assertEqual(other['acceptance']['kind'], 'trial')

    def test_a_hard_to_read_symbol_makes_the_readers_disagree(self):
        def uncertain_reader(payload):
            result = reconcile_answer(payload)
            result["transcript"]["ambiguities"] = ["The final digit could be 6 or 8."]
            return result
        response, names = self.grade(INCONSISTENT, mathpix=CORRECT, reader=uncertain_reader)  # The image does not settle the OCR disagreement.
        result, signals = response["result"], response["result"]["signals"]
        self.assertGreaterEqual(signals["risk"]["components"]["ocr"]["disagreements"], 1)
        self.assertIn("OCR readings still disagree; confirm the unresolved reading.", result["reasons"])
        self.assertIn("Independent readers disagree on what is written.", signals["escalation"]["considered"])
        self.assertFalse(signals["escalation"]["escalated"])
        self.assertIn(result["state"], ("ocr_review", "both"))
        with connect(self.path) as db:  # The disagreement is recorded, not resolved silently.
            reading = json.loads(db.execute("SELECT output_json FROM frq_steps WHERE stage='recognizing'").fetchone()[0])
        self.assertEqual([c["provider"] for c in reading["candidates"]], ["glm", "mathpix"])
        self.assertTrue(reading["consensus"]["disagreements"])

    def test_a_blank_response_is_handled_safely(self):
        response, names = self.grade([], primary=("R4",))
        result = response["result"]
        self.assertEqual(result["transcript"]["plain_text"], "")
        self.assertIn("No visible work was transcribed; confirm a blank or unreadable response.", result["reasons"])
        self.assertNotEqual(result["state"], "proposed")
        self.assertEqual(result["signals"]["answer"]["answers"], [])
        self.assertTrue(result["signals"]["risk"]["blocks"])

    def test_a_crossed_out_answer_is_never_taken_as_the_final_answer(self):
        def reader(payload):
            value = reconcile_answer(payload)
            value["transcript"]["lines"][2]["elements"][0]["state"] = "crossed_out"  # The student struck "x = 8" and wrote "x = 6" below.
            return value
        lines = ["3x - 8 = 10", "3x = 18", "x = 8", "x = 6"]
        response, names = self.grade(lines, reader=reader, answer_line=2)  # The scripted grader wrongly quotes the crossed-out line.
        result, signals = response["result"], response["result"]["signals"]
        self.assertEqual(result["transcript"]["lines"][2]["elements"][0]["state"], "crossed_out")  # Kept as written, never deleted.
        self.assertIn("A reported final answer is on a line the student crossed out.", result["reasons"])
        self.assertEqual(signals["answer"]["answers"], [])
        self.assertNotEqual(result["state"], "proposed")
        self.assertEqual(signals["risk"]["components"]["reading"]["crossed_out"], 1)
        self.assertNotIn("The response has crossed-out work.", signals["escalation"]["reasons"])
        self.assertEqual({c["result"] for c in signals["symbolic"]["checks"]}, {"pass"})  # Struck work is not part of the solution: 3x = 18, x = 6 is consistent.


class Disagreement(Pipeline):
    def test_grader_disagreement_names_the_lines_and_goes_to_individual_review(self):
        response, names = self.grade(CORRECT, primary=("R1",), second=("R2",))
        result, signals = response["result"], response["result"]["signals"]
        self.assertEqual((signals["second"]["applications"][1]["applied"], signals["risk"]["components"]["second"]["disagreements"]), (True, ["R1", "R2"]))
        self.assertTrue(any("disagree on R1, R2" in reason for reason in result["reasons"]))
        self.assertFalse(signals["escalation"]["escalated"])
        self.assertNotIn("adversarial", names)
        self.assertEqual(result["state"], "grading_review")
        self.assertEqual(self.proposal(response), ["R1"])  # The primary's evidence is never overwritten by another opinion.
        listing = self.frq.answer_groups(self.plan["id"], self.qid)
        self.assertEqual((listing["groups"], listing["individual"]), ([], 1))  # Disagreements are not part of any group.

    def test_equal_totals_do_not_hide_different_rubric_selections(self):
        response, names = self.grade(CORRECT, primary=('R1',), second=('R1', 'R2', 'R4'))
        result, signals = response['result'], response['result']['signals']
        self.assertEqual((result['state'], result['score']['score'], self.proposal(response)), ('grading_review', 10, ['R1']))
        self.assertTrue(signals['risk']['components']['second']['same_score'])
        self.assertEqual(signals['risk']['components']['second']['disagreements'], ['R2', 'R4'])
        self.assertEqual((signals['risk']['level'], signals['risk']['individual']), ('high', True))
        self.assertFalse(signals['escalation']['escalated'])
        self.assertEqual(names.count('adversarial'), 0)
        self.assertIsNone(response['acceptance'])
        listing = self.frq.answer_groups(self.plan['id'], self.qid)
        self.assertEqual((listing['groups'], listing['individual']), ([], 1))

    def test_equal_score_disagreements_still_require_review(self):
        response, names = self.grade(CORRECT, second=('R1', 'R2'))
        signals = response['result']['signals']
        self.assertFalse(signals['escalation']['escalated'])
        self.assertNotIn('adversarial', names)
        self.assertTrue(signals['risk']['individual'])
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertIsNone(response['acceptance'])

    def test_historical_equal_score_disagreement_is_projected_without_rewriting_receipts_or_human_grades(self):
        current_assess = risk.assess
        def legacy_assess(components, policy, contradictions=()):
            verdict = current_assess(components, policy, contradictions)
            if risk.selection_disagreement(components):
                verdict.update(level='low', individual=False, escalate=[], reasons=[],
                               blocks=['The two graders reach the same score by different rubric lines.'])
            return verdict
        with patch('grading.risk.POLICY', 'risk-rules-v4'), patch('grading.risk.assess', side_effect=legacy_assess):
            old, _ = self.grade(CORRECT, second=('R1', 'R2'))
            self.assertEqual(old['result']['state'], 'proposed')
        with connect(self.path) as db:
            before = dict(db.execute('SELECT * FROM frq_results WHERE id=?', (old['current_result_id'],)).fetchone())
        after = self.frq.response(old['id'])
        self.assertEqual(after['result']['state'], 'grading_review')
        self.assertEqual(after['result']['signals']['risk']['policy'], 'risk-rules-v4')
        self.assertEqual(after['result']['signals']['rubric_disagreement_review']['policy'], risk.POLICY)
        self.assertEqual(after['result']['score'], old['result']['score'])
        self.assertEqual(after['result']['applications'], old['result']['applications'])
        self.assertEqual(self.frq.responses(self.plan['id'])[0]['state'], 'grading_review')
        self.assertEqual(self.frq.answer_groups(self.plan['id'], self.qid)['individual'], 1)
        self.frq.save_review(after['id'], {'version': after['version'], 'result_id': after['current_result_id'],
            'status': 'approved', 'confirm_review': True, 'selected_items': ['R1']})
        self.assertEqual(self.frq.responses(self.plan['id'])[0]['state'], 'approved')
        flags = self.frq.metrics(self.plan['id'])['review_signals']['groups'][0]['flags']
        self.assertIn('Graders disagree on rubric selections', flags)
        self.assertNotIn('Graders disagree on score', flags)
        with connect(self.path) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM frq_results WHERE id=?', (old['current_result_id'],)).fetchone()), before)

    def test_one_response_can_be_sent_for_regrading_without_touching_the_instructors_decision(self):
        response, _ = self.grade(CORRECT)
        saved = self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": "draft", "selected_items": ["R2"], "comment": "Checking"})
        self.host.requests.clear()
        # A regrade that names no mode is a Cloud batch of one (on this fixture's team).
        command = {"request_id": str(uuid.uuid4()), "operation": "grade"}
        with patch("grading.standard.freeze"):
            job = self.frq.regrade_response(response["id"], command)
        self.assertEqual(self.frq.regrade_response(response["id"], command)["id"], job["id"])  # The same request never queues twice.
        for changed in ({"operation": "recognize"}, {"mode": "local"}):
            with self.assertRaises(Conflict):
                self.frq.regrade_response(response["id"], {**command, **changed})
        self.assertEqual((job["tier"], job["progress"]["total"]), ("cloud_standard", 1))
        self.run_all()
        names = [prompt_name(r["body"]) for r in self.host.requests]
        self.assertEqual(names, ["grade", "transcript_grade"])  # A new regrade uses the current conditional workflow and retained reading.
        after = self.frq.response(response["id"])
        self.assertNotEqual(after["current_result_id"], response["current_result_id"])
        # A regrade the instructor started replaces their earlier decision, a draft included, when every check passes the new grade
        # (acceptance.REVIEW_RULE, requirement, 2026-09-27): the review row is kept and an event names it; nothing is erased.
        self.assertIsNone(after["review"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status FROM frq_reviews WHERE id=?", (saved["review"]["id"],)).fetchone()[0], "draft")
            events = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='review_superseded_by_regrade'")]
        self.assertEqual([(e["response_id"], e["review_id"], e["review_status"]) for e in events], [(response["id"], saved["review"]["id"], "draft")])

class InstructorRules(Pipeline):
    def test_no_match_agreement_or_symbolic_result_selects_a_rubric_line_or_approves_a_response(self):
        for lines, chosen in ((CORRECT, ("R2",)), (INCONSISTENT, ("R1",)), (BARE, ("R4",))):
            with self.subTest(lines=lines):
                response, _ = self.grade(lines, primary=chosen)
                # Whatever the key, the second grader, the reviewer and the algebra say, the proposal is the primary grader's own validated lines.
                self.assertEqual(self.proposal(response), sorted(chosen))
                self.assertIsNone(response["review"])
                with connect(self.path) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_reviews").fetchone()[0], 0)
                    db.execute("UPDATE frq_tasks SET status='complete' WHERE status!='complete'")
                self.host.requests.clear()

    def test_every_opinion_is_stored_independently_and_the_audit_can_answer_who_said_what(self):
        response, _ = self.grade(CORRECT)
        with connect(self.path) as db:
            steps = {r["stage"]: json.loads(r["provider_json"]) for r in db.execute("SELECT stage,provider_json FROM frq_steps")}
        self.assertTrue({"grade:primary", "grade:second", "grade:check", "grading", "scoring"} <= set(steps))
        self.assertEqual((steps["grade:primary"]["prompt"], steps["grade:second"]["prompt"], steps["reconciling"]["prompt"]),
                         tuple(f"{name}-v{VERSIONS[name]}.txt" for name in ("grade", "transcript_grade", "reconcile")))  # the versions in use
        for name in ("grade:primary", "grade:second"):
            self.assertTrue(steps[name]["prompt_sha256"] and steps[name]["output_schema_sha256"] and steps[name]["cloud_call_id"])
        second = next(r for r in self.host.requests if prompt_name(r["body"]) == "transcript_grade")
        self.assertIsInstance(second["body"]["messages"][1]["content"], str)  # The transcript grader receives no image ...
        self.assertNotIn("proposed_applications", second["body"]["messages"][1]["content"])  # ... and not the primary grader's decisions.


if __name__ == "__main__":
    unittest.main()
