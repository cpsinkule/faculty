"""Regression tests for the 2026-09-22 review of scoring, rubric finalization, acceptance and the benchmark.

Synthetic rubrics, loopback models and scripted graders only; no saved student data or live inference.
"""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from grading import benchmark, config, evaluate, rubrics
from grading.models import plain_transcript
from grading.providers.base import ProviderError
from grading.rubric_quality import FULL_CREDIT_RULE, full_credit_alone, heaviest_combination
from rosters import connect
from fake_openai import grade_answer, images_of, payload_of
import test_grading_acceptance as acceptance
import test_grading_pipeline as pipeline
import test_grading_rubric_quality as quality
from test_grading_cloud import endpoint
from test_grading_pipeline import CORRECT, Pipeline


def line(i, points, description="", **more):
    return {"id": i, "points": points, "description": description or i, "group": None, "requires": [], "excludes": [], "stackable": True,
            "follow_through": False, "repeat_penalty_for_same_originating_error": True, **more}


class ManualGrades(acceptance.AcceptanceCase):
    """Item 1: a manual score is an adjustment of the UNCLAMPED sum, because adjustments are added before the point bounds."""

    def test_a_manual_score_is_saved_when_the_selection_was_clamped(self):
        response, _ = self.grade(CORRECT)
        # R1 (10) with R2 (4) is 14 before the ceiling makes it 10; a manual 6 needs -8, not -4.
        saved = self.review(response["id"], ["R1", "R2"], status="manual", manual_score=6, comment="Half the setup was copied.")
        self.assertEqual((saved["review"]["status"], saved["review"]["score"]["score"], saved["review"]["score"]["unclamped_score"]), ("manual", 6, 6))
        self.assertEqual(saved["review"]["score"]["adjustments"][-1], {"points": -8.0, "reason": "Half the setup was copied."})

    def test_exact_decimals_and_a_manual_score_too_far_from_the_lines(self):
        response, _ = self.grade(CORRECT)
        # 4 + 2 = 6, a manual 5.9 is -0.1 exactly (float subtraction would leave 5.8999... and refuse it).
        saved = self.review(response["id"], ["R2", "R3"], status="manual", manual_score=5.9, comment="Rounded work.")
        self.assertEqual(saved["review"]["score"]["score"], 5.9)
        with self.assertRaisesRegex(ValueError, "too far from a manual score"):
            # 10 + 4 + 2 = 16 before the ceiling: a manual 0 would need -16, beyond one adjustment of at most 10.
            self.review(response["id"], ["R1", "R2", "R3"], status="manual", manual_score=0, comment="Nothing is right.")


NEGATIVE = {"mode": "negative", "items": [
    {"id": "D1", "description": "Sign error", "points": -6},
    {"id": "D2", "description": "Arithmetic error", "points": -5},
    {"id": "D3", "description": "No work shown", "points": -1}]}


class DeductingManualGrades(acceptance.AcceptanceCase):
    def setUp(self):
        with patch.object(pipeline, "RULES", NEGATIVE):  # The pipeline fixture imports its module's rubric; this one deducts from 10.
            super().setUp()

    def test_a_manual_score_is_saved_when_deductions_went_below_the_floor(self):
        response, _ = self.grade(CORRECT, primary=("D3",))
        # 10 - 6 - 5 is -1 before the floor makes it 0; a manual 2 needs +3, not +2.
        saved = self.review(response["id"], ["D1", "D2"], status="manual", manual_score=2, comment="The second error follows from the first.")
        self.assertEqual((saved["review"]["score"]["score"], saved["review"]["score"]["adjustments"][-1]["points"]), (2, 3.0))


class ListedDisagreement(Pipeline):
    """Item 2: the response list applies the committee's resolution, as the review card and the answer groups already did."""
    legacy_workflow = False

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        s = saved["settings"]
        s["tiers"]["cloud_standard"]["verifier"] = "gamma-vision"
        self.frq.save_settings({"version": saved["version"], "settings": s})

    def test_a_response_settled_on_credit_bearing_lines_is_final_everywhere(self):
        def script(name, body):
            if name == "reconcile":
                return None
            chosen = {"alpha-vision": ("R1",), "beta-text": ("R2",), "gamma-vision": ("R1", "R4")}[body["model"].removeprefix("fixture/")]
            return grade_answer(payload_of(body), chosen, image=bool(images_of(body)))
        response, _ = self.grade(CORRECT, script=script)
        committee = response["result"]["signals"]["committee"]
        self.assertEqual(committee["status"], "resolved")
        self.assertIs(committee["pair"]["agrees"], False)  # The pair itself differed; the additional reviewer settled it.
        self.assertIs(response["result"]["signals"]["risk"]["components"]["second"]["agrees"], False)
        listed = self.frq.responses(self.plan["id"])[0]
        self.assertEqual(listed["state"], "auto_accepted")
        self.assertEqual(self.frq.review_queue(self.plan["id"])["waiting"], 0)
        self.frq.export(self.plan["id"], "csv")
        workflow = self.frq.assignment(self.plan["id"])["workflow"]
        self.assertEqual(workflow["progress"]["review"]["completed"], workflow["progress"]["review"]["total"])
        self.assertTrue(workflow["export"]["current"])
        self.assertEqual(workflow["certification"]["state"], "none")  # exporting never certifies

    def test_the_cheap_reading_matches_the_rule(self):
        from grading.review import ReviewMixin
        disagrees = ReviewMixin.selection_disagrees
        resolved = json.dumps({"status": "resolved", "pair": {"agrees": False}})
        unresolved = json.dumps({"status": "unresolved", "pair": {"agrees": False}, "third_comparisons": {}})
        missing = json.dumps({"status": "unresolved", "pair": None, "third_comparisons": {}})
        self.assertEqual([disagrees(resolved, 0, None), disagrees(unresolved, 1, None), disagrees(missing, 0, None)], [False, True, False])
        self.assertEqual([disagrees(None, 0, None), disagrees(None, 1, 0), disagrees(None, 1, 1), disagrees(None, None, None)], [True, True, False, False])


class FullCreditAlone(unittest.TestCase):
    """Item 4: a zero line beside the full-credit line and an unscorable selection are left as they are; v4 flags full marks
    reached by the steps where the grader said the full-credit requirements were not met."""

    def setUp(self):
        self.rubric = {"mode": "positive", "floor": 0, "ceiling": 10, "groups": [], "items": [
            line("R1", 10, "Correct answer with work"), line("R2", 4, "Correct setup"), line("R3", 6, "Correct solution of the setup"),
            line("R4", 0, "No Answer or Completely Incorrect"), line("R5", 0, "Part B: No answer or completely incorrect")]}
        self.entry = {"builtin": [{"item_id": "R1", "rule": "full_credit"}, {"item_id": "R4", "rule": "no_credit"}], "pairs": []}

    def graded(self, *ticked, score=10, eligible=True):
        return {"applications": [{"rubric_item_id": i["id"], "applied": i["id"] in ticked, "reason": "as read", "evidence": []} for i in self.rubric["items"]],
                "full_credit_eligible": eligible, "score": None if score is None else {"score": score}, "review_reasons": ["R5: the evidence is not quoted."]}

    def test_a_zero_line_beside_the_full_credit_line_is_a_contradiction_to_look_at(self):
        for rule in ("full-credit-alone-v1", "full-credit-alone-v2", "full-credit-alone-v3", FULL_CREDIT_RULE):
            with self.subTest(rule=rule):
                stacked = self.graded("R1", "R5")
                self.assertIs(full_credit_alone(stacked, self.entry, self.rubric, 10, rule), stacked)
        # A step line beside it is still read as the full-credit line alone.
        read = full_credit_alone(self.graded("R1", "R2"), self.entry, self.rubric, 10)
        self.assertEqual([a["rubric_item_id"] for a in read["applications"] if a["applied"]], ["R1"])

    def test_an_unscorable_selection_is_never_turned_into_a_scored_one(self):
        for rule in ("full-credit-alone-v1", FULL_CREDIT_RULE):
            with self.subTest(rule=rule):
                unscorable = self.graded("R1", "R2", score=None)
                self.assertIs(full_credit_alone(unscorable, self.entry, self.rubric, 10, rule), unscorable)

    def test_v4_flags_full_marks_by_the_steps_when_the_grader_said_the_requirements_were_not_met(self):
        steps = self.graded("R2", "R3", eligible=False)
        read = full_credit_alone(steps, self.entry, self.rubric, 10)
        self.assertEqual((read["applications"], read["score"]), (steps["applications"], steps["score"]))  # Ticks and score as the grader gave them.
        self.assertIn("add up to full credit, but it said the full-credit item's requirements were not satisfied", " ".join(read["review_reasons"]))
        for untouched in (self.graded("R2", "R3", eligible=None), self.graded("R2", score=4, eligible=False)):  # Not said, or not full marks.
            self.assertIs(full_credit_alone(untouched, self.entry, self.rubric, 10), untouched)
        self.assertIs(full_credit_alone(steps, None, self.rubric, 10), steps)  # No finalization: no full-credit line to speak of.
        # Frozen earlier rules keep their reading: v3 promotes the steps, v1 leaves them.
        self.assertEqual(full_credit_alone(steps, self.entry, self.rubric, 10, "full-credit-alone-v3")["normalized"]["route"], "steps")
        self.assertIs(full_credit_alone(steps, self.entry, self.rubric, 10, "full-credit-alone-v1"), steps)


class HeaviestCombination(unittest.TestCase):
    """Item 7: the search is exact, and a search that reaches its limit is undecided, never resolved."""

    def test_the_shared_cases_hold_both_new_outcomes(self):
        cases = json.loads((Path(__file__).parent / "fixtures" / "grading" / "overflow_cases.json").read_text())
        found = {case["name"]: case for case in cases}
        exact = next(c for n, c in found.items() if n.startswith("an exact search finds"))
        self.assertEqual(heaviest_combination(exact["rubric"], exact["entry"], exact["maximum"])["points"], 22.5)
        undecided = next(c for n, c in found.items() if n.startswith("a search that reaches its limit"))
        self.assertEqual(heaviest_combination(undecided["rubric"], undecided["entry"], undecided["maximum"]),
                         {"points": None, "maximum": 16, "lines": [], "undecided": True})

    def test_an_answer_is_remembered_by_the_content_searched_and_each_caller_gets_its_own_copy(self):
        # PERFORMANCE "PROCESS-WIDE CACHES" (2026-10-27): every final_decisions asked it again for every question, 45 ms on Exam 2.
        from grading import rubric_quality
        cases = json.loads((Path(__file__).parent / "fixtures" / "grading" / "overflow_cases.json").read_text())
        exact = next(c for c in cases if c["name"].startswith("an exact search finds"))
        with patch.object(rubric_quality, "_COMBINATIONS", rubric_quality.OrderedDict()), patch.object(rubric_quality, "COMBINATIONS_MAX", 2), \
                patch.object(rubric_quality, "_heaviest_combination", wraps=rubric_quality._heaviest_combination) as search:
            first = heaviest_combination(exact["rubric"], exact["entry"], exact["maximum"])
            first["lines"].append("changed by a caller")
            again = heaviest_combination(exact["rubric"], exact["entry"], exact["maximum"])
            self.assertEqual(search.call_count, 1)
            self.assertEqual(again["points"], 22.5)
            self.assertNotIn("changed by a caller", again["lines"])
            heaviest_combination(exact["rubric"], exact["entry"], exact["maximum"] + 100)  # another value is another search: within it
            self.assertEqual(search.call_count, 2)
            self.assertIsNone(heaviest_combination(exact["rubric"], exact["entry"], exact["maximum"] + 100))
            with patch.object(rubric_quality, "SEARCH_LIMIT", 1):  # the limit is part of what was searched
                heaviest_combination(exact["rubric"], exact["entry"], exact["maximum"])
            self.assertEqual(search.call_count, 3)
            self.assertEqual(len(rubric_quality._COMBINATIONS), 2)  # bounded, least recently used first

    def test_an_undecided_question_holds_its_responses_and_the_confirmation_back(self):
        from grading.rubric_quality import overflow_text, quality_flags
        open_question = {"question_id": "q", "points": None, "maximum": 16, "lines": [], "undecided": True}
        reasons = quality_flags({"state": "needs_review", "review": None, "unresolved": [open_question]}, "q", "r", [])["reasons"]
        self.assertEqual(len(reasons), 1)
        self.assertIn("too entangled to prove that no selection exceeds 16 points", reasons[0])
        self.assertIn("too entangled", overflow_text(open_question))


class Finalization(Pipeline):
    """Items 5 and 6, through the real check: a corrected line's clarification, and a re-ask that fails."""
    check = quality.Quality.check
    confirm = quality.Quality.confirm

    def setUp(self):
        super().setUp()
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "rubric_reviewer": "beta-text"}})
        self.host.script = lambda body, host: quality.suggestion(payload_of(body))

    def test_a_corrected_line_does_not_carry_the_clarification_of_its_old_wording(self):
        aid = self.plan["id"]
        other = "Credit a correct answer without work only when the work is on another page."
        self.confirm(self.check(), pairs=[["R2", "R3"]], clarifications={"R2": quality.CLEAR, "R3": other})
        before = self.frq.assignment(aid)["questions"][0]
        self.frq.correct_rubric_line(aid, self.qid, {"version": before["version"], "rubric_id": before["rubric_id"], "sha256": before["rubric"]["sha256"],
            "item_id": "R2", "description": "Sets up the equation 3x - 8 = 10 correctly"})
        status = self.frq.rubric_quality(aid)
        entry = status["review"]["policy"]["questions"][0]
        self.assertEqual((status["state"], entry["pairs"], entry["clarifications"]), ("confirmed", [["R2", "R3"]], {"R3": other}))
        self.assertEqual(status["review"]["policy"]["carried"][-1]["clarification_dropped"], quality.CLEAR)
        with connect(self.path) as db:
            detail = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='rubric_quality_carried' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(detail["clarification_dropped"], quality.CLEAR)
        # What the graders read: the corrected wording, and no clarification written against the old one.
        from grading.rubric_quality import grader_rubric
        rubric = self.frq.assignment(aid)["questions"][0]["rubric"]["rules"]
        read = {i["id"]: i for i in grader_rubric(rubric, entry)["items"]}
        self.assertNotIn("clarification", read["R2"])
        self.assertEqual(read["R3"]["clarification"], other)

    def test_a_failed_reask_keeps_the_first_answer_and_reports_what_is_open(self):
        aid = self.plan["id"]
        q = self.frq.assignment(aid)["questions"][0]
        rules = {**q["rubric"]["rules"], "items": [*q["rubric"]["rules"]["items"], {"id": "R5", "description": "Correct setup with a sign slip", "points": 3.5},
                                                   {"id": "R6", "description": "Correct setup in another form", "points": 4}]}
        self.frq.import_rubric(aid, self.qid, {"version": q["version"], "source_text": "Steps worth more than the question", "rules": rules})
        real = self.frq.cloud_call

        def flaky(owner, role, endpoint_, request, settings):
            if owner["task_attempt"] == 1:
                raise ProviderError("This request failed 3 times with a confirmed provider error. It stopped at the attempt cap.")
            return real(owner, role, endpoint_, request, settings)
        with patch.object(self.frq, "cloud_call", side_effect=flaky):
            checked = self.check()
        self.assertEqual((checked["check"]["status"], checked["state"]), ("complete", "needs_review"))
        still, = checked["check"]["result"]["unresolved"]
        self.assertEqual((still["lines"], still["points"]), (["R2", "R5", "R6"], 11.5))
        self.assertIn("could not be asked again", checked["check"]["result"]["summary"])
        with connect(self.path) as db:
            provider = json.loads(db.execute("SELECT provider_json FROM frq_rubric_quality_checks ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertIn("attempt cap", provider["questions"][self.qid]["reask_error"])


class WordingRules(unittest.TestCase):
    """Item 8: "except", "maximum", "at most" and "no more than" set a condition only for caps and other lines."""

    def test_descriptive_words_do_not_make_a_line_conditional(self):
        described = ["Part A: Correct except for one entry", "States the maximum profit", "States that the system has at most one solution",
                     "Correct except for a sign error", "No more than two decimal places are shown"]
        conditions = ["Arithmetic slips: no more than 2 points in total", "At most one of the form items applies.", "Maximum of 2 points for part (a)",
                      "Deduct 1 point per error, maximum 3", "2 points maximum for units", "Award except when R2 is selected", "Full credit except for part b",
                      "Award unless the final answer item is selected", "Either this item or the next, but not both", "Apply only if part a was attempted"]
        for wording in described:
            self.assertFalse(rubrics.CONDITIONAL.search(wording.lower()), wording)
            self.assertFalse(rubrics.conditional({"description": wording, "exceptions": wording}), wording)  # An older import's own mark.
        for wording in conditions:
            self.assertTrue(rubrics.CONDITIONAL.search(wording.lower()), wording)
        rows = [{"id": f"R{n}", "description": d} for n, d in enumerate(described + conditions[:2], 1)]
        warnings = []
        rubrics.wording_rules(rows, warnings)
        self.assertEqual([r["id"] for r in rows if r.get("exceptions")], [f"R{len(described) + 1}", f"R{len(described) + 2}"])
        self.assertEqual(len(warnings), 2)


class Permissions(acceptance.AcceptanceCase):
    """Item 9: one permission per scope, and revoking or suspending ends every one of them."""
    evidence = acceptance.TheGate.evidence
    other_student = acceptance.TheGate.other_student

    def permissions(self, scope):
        with connect(self.path) as db:
            return [(r[0], db.execute("SELECT kind FROM frq_acceptance_permission_events WHERE permission_id=? ORDER BY rowid DESC LIMIT 1", (r[0],)).fetchone()[0])
                    for r in db.execute("SELECT id FROM frq_acceptance_permissions WHERE scope_sha256=? ORDER BY rowid", (scope,))]

    def legacy_double(self, scope):
        """A second enabled permission of the same scope, as a double submit made before the fix could leave."""
        with connect(self.path) as db:
            first = dict(db.execute("SELECT * FROM frq_acceptance_permissions WHERE scope_sha256=?", (scope,)).fetchone())
            db.execute("INSERT INTO frq_acceptance_permissions VALUES(?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), first["assignment_id"], first["question_id"], scope,
                       first["report_id"], first["error_limit"], first["audit_share"], first["created_at"]))
            second = db.execute("SELECT id FROM frq_acceptance_permissions ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            self.frq._permission_event(db, second, "enabled", "", "workspace")

    def test_a_second_enable_of_the_same_scope_creates_nothing(self):
        self.configure_acceptance(blind_share=1.0)
        scope = self.evidence(clean=300)
        command = {"scope_sha256": scope, "confirm_automatic_acceptance": True}
        self.frq.enable_acceptance(self.plan["id"], self.qid, command)
        again = self.frq.enable_acceptance(self.plan["id"], self.qid, command)["reports"][0]
        self.assertEqual([kind for _, kind in self.permissions(scope)], ["enabled"])
        self.assertEqual(again["permission"]["status"], "enabled")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_benchmark_reports").fetchone()[0], 1)

    def test_revoking_ends_every_permission_of_the_scope(self):
        self.configure_acceptance(blind_share=1.0)
        scope = self.evidence(clean=300)
        self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope, "confirm_automatic_acceptance": True})
        self.legacy_double(scope)
        self.frq.disable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope})
        self.assertEqual([kind for _, kind in self.permissions(scope)], ["revoked", "revoked"])
        with connect(self.path) as db:
            self.assertIsNone(self.frq.permission(db, scope))

    def test_an_overturned_audit_suspends_every_permission_of_its_scope(self):
        self.configure_acceptance(blind_share=1.0, audit_share=1.0)
        scope = self.evidence(clean=300)
        self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope, "confirm_automatic_acceptance": True})
        self.legacy_double(scope)
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.other_student()), changed="second")
        self.queue()
        self.run_all()
        audited = next(a for a in self.acceptances() if a["kind"] == "automatic")
        self.review(audited["response_id"], ["R2"], comment="Audit: the reading was wrong.")
        self.assertEqual([kind for _, kind in self.permissions(scope)], ["suspended", "suspended"])
        with connect(self.path) as db:
            self.assertIsNone(self.frq.permission(db, scope))


def record(response_id, expected, proposed=None, *, student="s1", split=None, transcript="x = 6"):
    points = {"R1": 10, "R2": 4}
    apps = lambda chosen: [{"rubric_item_id": i, "applied": i in chosen} for i in ("R1", "R2")]
    total = lambda chosen: {"score": sum(points[i] for i in chosen)}
    row = {"response_id": response_id, "student": student, "question": {"max_points": 10}, "rubric": {"items": [line("R1", 10), line("R2", 4)], "warnings": []},
           "final": {"transcript": plain_transcript(transcript), "applications": apps(expected), "score": total(expected), "comment": ""},
           "proposal": {"transcript": plain_transcript(transcript), "applications": apps(proposed or expected), "score": total(proposed or expected), "state": "proposed"}}
    if split:
        row["split"] = split
    return row


class Statistics(unittest.TestCase):
    """Item 10: exact paired comparisons, failures counted as wrong, and a baseline of the same role and transcript source."""

    def test_four_to_nothing_in_forty_is_within_noise(self):
        baseline = {f"r{n}": n >= 4 for n in range(40)}
        found = benchmark.paired(baseline, {f"r{n}": True for n in range(40)})
        self.assertEqual((found["only_challenger_right"], found["only_baseline_right"], found["p_value"]), (4, 0, .125))
        self.assertEqual(found["verdict"], "within noise: keep the default")
        self.assertLess(found["interval_95"][0], 0)
        # The exact test and its interval agree for every split of the discordant responses.
        for right in range(0, 25):
            for wrong in range(0, 25):
                base = {**{f"d{i}": i < wrong for i in range(right + wrong)}, "same": True}
                mine = {**{f"d{i}": i >= wrong for i in range(right + wrong)}, "same": True}
                result = benchmark.paired(base, mine)
                self.assertEqual(result["p_value"] < .05, result["verdict"] != "within noise: keep the default", (right, wrong))

    def test_failed_predictions_count_as_wrong_and_are_reported(self):
        rows = [{"expected": r["final"], "proposed": r["proposal"], "rubric": r["rubric"], "response_id": r["response_id"]} for r in
                [record(f"r{n}", {"R1"}) for n in range(20)]]
        failures = [{**row, "proposed": {"transcript": row["proposed"]["transcript"]}, "failed": True} for row in rows[:10]]
        graded = benchmark.grading(failures + rows[10:])
        self.assertEqual((graded["scored_responses"], graded["failed_responses"], graded["exact_score_agreement"]), (10, 10, .5))
        base, _ = benchmark.verdicts(rows)
        mine, failed = benchmark.verdicts(failures + rows[10:])
        compared = benchmark.paired(base, mine, (), failed)
        self.assertEqual((compared["responses"], compared["challenger_agreement"], compared["challenger_failed"]), (20, .5, 10))
        unscorable = {**rows[0], "proposed": {**rows[0]["proposed"], "score": None}}
        self.assertTrue(benchmark.failed(unscorable))
        self.assertFalse(benchmark.failed({**rows[0], "proposed": {"transcript": rows[0]["proposed"]["transcript"]}}))  # A reading-only candidate.

    def test_the_baseline_is_named_exactly_and_shares_the_role_and_transcript_source(self):
        records = [record(f"r{n}", {"R1"}) for n in range(6)]
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir())) as root:
            dataset = Path(root) / "dataset.json"
            dataset.write_text(json.dumps({"records": records}))
            files = []
            for name, transcript, chosen, failed_ in (("qwen3-vl-32b", "proposal", {"R1"}, 0), ("qwen3-vl-32b", "human", {"R1"}, 0),
                                                      ("challenger", "proposal", {"R2"}, 0), ("challenger", "human", {"R1"}, 3), ("qwen3-vl-32b-mini", "human", {"R1"}, 0)):
                predictions = {}
                for n, r in enumerate(records):
                    applied = [{"rubric_item_id": i, "applied": i in chosen} for i in ("R1", "R2")]
                    predictions[r["response_id"]] = ({"transcript": r["proposal"]["transcript"], "failed": "refused", "malformed": True} if n < failed_ else
                                                      {"transcript": r["proposal"]["transcript"], "applications": applied, "score": {"score": 10 if "R1" in chosen else 4}, "state": "proposed"})
                files.append(Path(root) / f"{name}-{transcript}.json")
                files[-1].write_text(json.dumps({"endpoint": name, "model": "fixture/" + name, "lineage": name, "role": "primary", "transcript": transcript, "predictions": predictions}))
            found = evaluate.report(dataset, files, baseline="qwen3-vl-32b")
        self.assertEqual(found["format"], "faculty-frq-benchmark-v3")
        self.assertEqual(found["baselines"], {"primary, proposal transcript": "qwen3-vl-32b (primary, proposal transcript)",
                                              "primary, human transcript": "qwen3-vl-32b (primary, human transcript)"})
        human = found["against_baseline"]["challenger (primary, human transcript)"]
        self.assertEqual((human["baseline"], human["challenger_failed"], human["challenger_agreement"]), ("qwen3-vl-32b (primary, human transcript)", 3, .5))
        self.assertEqual(found["against_baseline"]["challenger (primary, proposal transcript)"]["baseline"], "qwen3-vl-32b (primary, proposal transcript)")
        self.assertIn("qwen3-vl-32b-mini (primary, human transcript)", found["against_baseline"])  # A prefix is not the baseline's name.
        self.assertEqual(found["profiles"]["challenger (primary, human transcript)"]["grading"]["failed_responses"], 3)


class StableSplits(acceptance.AcceptanceCase):
    """Item 11: a student's split is the same in every export; `--split` reads one split."""

    def test_the_split_follows_the_student_not_the_export(self):
        self.configure_acceptance(blind_share=0.0)
        response, _ = self.grade(CORRECT)
        self.review(response["id"], ["R1"])
        exported = []
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir())) as root:
            for n in range(2):
                stream, _, _ = self.frq.export(self.plan["id"], "dataset")
                path = Path(root) / f"dataset-{n}.zip"
                path.write_bytes(stream.read())
                stream.close()
                exported.append(evaluate.load(path)["records"][0])
            first, second = exported
            self.assertNotEqual(first["student"], second["student"])  # The pseudonym still cannot join two exports.
            self.assertEqual(first["split"], second["split"])
            self.assertEqual(first["split"], benchmark.enrollment_split(self.enrollment))
            report = evaluate.report(Path(root) / "dataset-0.zip", split=first["split"])
            other = next(name for name, _ in benchmark.SPLITS if name != first["split"])
            self.assertEqual((report["responses"], evaluate.report(Path(root) / "dataset-0.zip", split=other)["responses"]), (1, 0))

    def test_a_returning_student_keeps_their_split_and_old_datasets_still_split(self):
        # The first five digits are the student's workspace prefix, the same in every course and term.
        self.assertEqual(benchmark.enrollment_split("4821300017"), benchmark.enrollment_split("4821399342"))
        sizes = {}
        for prefix in range(10000, 13000):
            sizes[benchmark.enrollment_split(f"{prefix}00000")] = sizes.get(benchmark.enrollment_split(f"{prefix}00000"), 0) + 1
        self.assertTrue(1650 < sizes["train"] < 1950 and 480 < sizes["test"] < 720, sizes)
        self.assertEqual(benchmark.record_split({"student": "abc"}), benchmark.split_of("abc"))  # Exported before the field existed.
        self.assertEqual(benchmark.record_split({"student": "abc", "split": "test"}), "test")
        self.assertIsNone(benchmark.record_split({}))

    def test_run_endpoint_reads_one_split(self):
        records = [record("r1", {"R1"}, split="train"), record("r2", {"R1"}, split="test")]
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir())) as root:
            dataset = Path(root) / "dataset.json"
            dataset.write_text(json.dumps({"records": records}))
            sent = []

            class Provider:
                def submit(self, request):
                    sent.append(request["payload"]["response_id"])
                    return {"result": {"output": grade_answer(request["payload"], {"R1"}, image=False), "metadata": {}}}

                def cost(self, usage):
                    return None, "not billed"
            with patch("grading.evaluate.providers.provider", return_value=Provider()):
                result = evaluate.run_endpoint(dataset, {**config.ENDPOINT, "name": "x", "model": "m", "lineage": "l", "modality": "text", "max_inflight": 1}, split="test")
        self.assertEqual((set(sent), list(result["predictions"]), result["split"]), ({"r2"}, ["r2"], "test"))  # The train student is never sent.


class SavedConnections(Pipeline):
    """Coordinator follow-up: a key bound to another host is refused when SAVED; stored settings still load."""

    def test_saving_a_connection_that_would_send_its_key_elsewhere_is_refused(self):
        current = self.frq.settings()
        for key in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY"):
            wrong = endpoint(self.host, "delta-vision", key_env=key)
            settings = {**current["settings"], "endpoints": [*current["settings"]["endpoints"], wrong]}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Connection delta-vision"):
                self.frq.save_settings({"version": current["version"], "settings": settings})
            self.assertTrue(config.validate(settings))  # Loading what may already be stored never fails.
        self.assertEqual(self.frq.settings()["version"], current["version"])


if __name__ == "__main__":
    unittest.main()
