"""GRADING MULTIPLE-CHOICE READER (2026-10-08): a question confirmed as multiple choice is read by OpenAI Decisions from the marked
option; a confident read is the grade (the instructor's exception to EVIDENCE, NEVER A GRADE), anything else is graded as written work.
Scripted readers only; nothing leaves the machine."""
import json
import unittest
from unittest.mock import patch

from grading import choice, recognition, risk
from rosters import connect
from test_grading_cloud import endpoint
from test_grading_pipeline import CORRECT, Pipeline

POSITIVE = {"mode": "positive", "items": [
    {"id": "R1", "description": "Correct choice: $$(b)$$ $$x = 6$$", "points": 10},
    {"id": "R2", "description": "No answer or completely incorrect", "points": 0}]}
NEGATIVE = {"mode": "negative", "items": [
    {"id": "R1", "description": "Correct choice: $$(c)$$ $$\\tfrac{1}{2}$$", "points": 0},
    {"id": "R2", "description": "Wrong choice, more than one choice, or no choice", "points": -2}]}
OPTIONS = ["(a)", "(b)", "(c)", "(d)"]


def read(first, second=None, p=.98, options=OPTIONS):
    """The two orders' answers as the shared client returns them (Jev's shape)."""
    def one(pick):
        criteria = [*options, choice.NONE, choice.SEVERAL]
        rest = (1 - p) / (len(criteria) - 1)
        probabilities = {o: p if o == pick else rest for o in criteria}
        n = len(criteria)
        return {"type": "choice", "choice": pick, "probabilities": probabilities, "confidence": (p - 1 / n) / (1 - 1 / n)}
    return {"marked": one(first), "marked__reversed": one(second or first)}


class ShapeTests(unittest.TestCase):
    def test_only_the_two_drafted_multiple_choice_shapes_are_read_by_choice(self):
        self.assertEqual(choice.lines(POSITIVE), {"correct": "R1", "wrong": "R2", "mode": "positive", "key": "(b)"})
        self.assertEqual(choice.lines(NEGATIVE), {"correct": "R1", "wrong": "R2", "mode": "negative", "key": "(c)"})
        three = {**POSITIVE, "items": POSITIVE["items"] + [{"id": "R3", "description": "Work shown", "points": 1}]}
        swapped = {**POSITIVE, "items": [{**POSITIVE["items"][0], "points": 0}, {**POSITIVE["items"][1], "points": 10}]}
        grouped = {**POSITIVE, "items": [{**POSITIVE["items"][0], "requires": ["R2"]}, POSITIVE["items"][1]]}
        for other in (three, swapped, grouped, {"mode": "positive", "items": []}, None):
            with self.subTest(other=other):
                self.assertIsNone(choice.lines(other))

    def test_faculty_proposes_from_the_rubric_and_the_printed_options(self):
        content = {"problem": "Solve 3x - 8 = 10. (a) 2 (b) 6 (c) 8 (d) 10", "regions": [{}]}
        self.assertEqual(choice.propose(content, POSITIVE, "r1"), {"options": OPTIONS, "correct": "(b)", "rubric_id": "r1", "source": "rubric"})
        self.assertIsNone(choice.propose({**content, "problem": "Solve. (a) 2 (c) 8"}, POSITIVE, "r1"))  # the key is not printed
        self.assertIsNone(choice.propose({**content, "problem": "Solve 3x - 8 = 10."}, POSITIVE, "r1"))  # no printed options
        self.assertIsNone(choice.propose({**content, "regions": [{}, {}]}, POSITIVE, "r1"))  # two answer areas

    def test_automatic_confirmation_follows_the_rule_unless_written_work_was_chosen(self):
        # AUTOMATIC CONFIRMATION (requirement, 2026-10-08): no click; the instructor may only set written work, or the rule back.
        content = {"problem": "Solve. (a) 2 (b) 6 (c) 8 (d) 10", "regions": [{}]}
        found = choice.resolved(content, POSITIVE, "r1")
        self.assertEqual(found, {"options": OPTIONS, "correct": "(b)", "rubric_id": "r1", "source": "rubric", "automatic": True, "rule": choice.RULE})
        self.assertIsNone(choice.resolved({**content, "choice_grading": {"written": True, "rule": choice.RULE}}, POSITIVE, "r1"))
        earlier = {"options": OPTIONS, "correct": "(b)", "rubric_id": "r0", "rule": choice.RULE}  # saved before the rule, another rubric
        self.assertIsNone(choice.resolved({**content, "choice_grading": earlier}, POSITIVE, "r1"))
        self.assertIsNone(choice.resolved(content, {**POSITIVE, "items": POSITIVE["items"][:1]}, "r1"))  # not the drafted shape
        self.assertEqual(choice.clean({"written": True}), {"written": True, "rule": choice.RULE})
        self.assertIsNone(choice.clean(None))
        with self.assertRaisesRegex(ValueError, "written work"):
            choice.clean({"options": OPTIONS, "correct": "(b)"})

    def test_a_finalization_clarification_does_not_keep_a_question_from_the_reader(self):
        # GRADING MULTIPLE-CHOICE READER (2026-10-17): the finalization clarifies every multiple-choice line; only written work holds it.
        content = {"problem": "Solve. (a) 2 (b) 6 (c) 8 (d) 10", "regions": [{}],
                   "rubric_guidance": {"clarifications": {"R1": "The choice is the option letter the student circles or writes."}}}
        settings = {choice.SETTING: True, "choice_reader_rule": choice.RULE}
        self.assertTrue(choice.applies(content, POSITIVE, "r1", 1, settings))
        self.assertFalse(choice.applies({**content, "choice_grading": {"written": True}}, POSITIVE, "r1", 1, settings))
        self.assertFalse(choice.applies(content, POSITIVE, "r1", 2, settings))  # two answer areas

    def test_the_read_ticks_the_line_its_letter_earns_in_either_rubric_mode(self):
        from grading.rubrics import validate
        cases = ((POSITIVE, "(b)", "R1", 10), (POSITIVE, "(c)", "R2", 0), (NEGATIVE, "(c)", "R1", 2), (NEGATIVE, "(a)", "R2", 0))
        for raw, letter, line, points in cases:
            with self.subTest(rubric=raw["mode"], letter=letter):
                maximum = 10 if raw["mode"] == "positive" else 2
                rubric = validate(raw, maximum)  # as a stored rubric reads
                decided = choice.decide(read(letter), OPTIONS, .8)
                graded = choice.grade(decided, {"correct": choice.lines(rubric)["key"], "options": OPTIONS}, rubric, maximum,
                                      recognition.validate_grade)
                self.assertEqual([a["rubric_item_id"] for a in graded["applications"] if a["applied"]], [line])
                self.assertEqual((graded["score"]["score"], graded["review_reasons"]), (points, []))
                self.assertEqual(graded["interpretation"]["final_answers"][0]["text"], letter)

    def test_a_read_is_confident_only_when_both_orders_name_one_option_at_the_cutoff(self):
        self.assertTrue(choice.decide(read("(b)"), OPTIONS, .8)["confident"])
        self.assertFalse(choice.decide(read("(b)", "(c)"), OPTIONS, .8)["confident"])  # the orders disagree
        self.assertFalse(choice.decide(read("(b)", p=.7), OPTIONS, .8)["confident"])  # under the cutoff
        self.assertFalse(choice.decide(read(choice.NONE), OPTIONS, .8)["confident"])
        self.assertFalse(choice.decide(read(choice.SEVERAL), OPTIONS, .8)["confident"])
        asked = choice.questions(OPTIONS)
        self.assertEqual(set(asked), {"marked", "marked__reversed"})
        self.assertEqual(list(asked["marked__reversed"]["criteria"])[0], choice.SEVERAL)  # the reversed order
        # GRADING MULTIPLE-CHOICE READER: the simple question of 2026-10-16 over the bare option labels.
        self.assertEqual(asked["marked"]["instructions"], "Which answer choice did the student select?")
        self.assertEqual({o: asked["marked"]["criteria"][o] for o in OPTIONS}, {o: o for o in OPTIONS})

    def test_the_risk_policy_waives_by_name_only_what_asks_for_ocr_or_a_second_grader(self):
        c = {"ocr": {"providers": 0, "agreement": None, "disagreements": 0, "min_confidence": None, "min_rate": None, "min_unrated_confidence": None},
             "reading": {"visual_confidence": .5, "ambiguities": 0, "crossed_out": 0, "unclear": 0, "registered": True},
             "primary": {"confidence": .5, "min_item_confidence": .5, "review_reasons": 0, "rubric_ambiguities": 0, "scored": True,
                         "work_consistent": "unclear", "attempts": 1, "ambiguous_items": 0},
             "answer": {"check": "match", "readers_agree": None, "work": "none", "work_required": False, "key": True, "full_credit": True},
             "symbolic": {"result": "not_run", "fail": 0, "pass": 0, "indeterminate": 0, "contradictions": []}, "second": None, "verifier": None,
             "output": {"rejected_outputs": 0}, "rubric": {"exceptions": False, "conditions": [], "warnings": 0},
             "committee": {"status": "resolved"}, "choice": {"rule": choice.RULE, "choice": "(b)", "confidence": .5, "orders_agree": True}}
        verdict = risk.assess(c, risk.DEFAULTS)
        self.assertEqual((verdict["blocks"], verdict["individual"]), ([], False))
        self.assertEqual(set(verdict["waived"]["choice_reader"]["blocks"]), {risk.NOBODY_ELSE, "No independent OCR reading stands beside the vision reader."})
        held = risk.assess({**c, "answer": {**c["answer"], "check": "mismatch"}}, risk.DEFAULTS)
        self.assertIn(risk.KEY_DIFFERS, held["blocks"])  # every other check stands
        unsure = risk.assess({**c, "choice": {**c["choice"], "orders_agree": False}}, risk.DEFAULTS)
        self.assertIn("The multiple-choice reader was unsure.", unsure["blocks"])


class ChoiceReaderTests(Pipeline):
    legacy_workflow = False  # the current Cloud team, as `test_grading_teams.GradingTeams` sets it up

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        s = saved["settings"]
        s["tiers"]["cloud_standard"]["verifier"] = "gamma-vision"
        s["endpoints"].extend([endpoint(self.host, "delta-vision"), endpoint(self.host, "epsilon-vision"), endpoint(self.host, "zeta-vision")])
        self.frq.save_settings({"version": saved["version"], "settings": s})
        # The statement prints its options (a new statement unpins the rubric, so it comes first); no key is saved, so a read is checked
        # against the rubric's own correct choice.
        plan = self.frq.assignment(self.plan["id"])
        self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": "q1", "assignment_version_id": self.version_id,
                                             "content": {**plan["questions"][0]["content"], "problem": "Solve 3x - 8 = 10. (a) 2 (b) 6 (c) 8 (d) 10"}})
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        q = self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "Quiz rubric", "rules": POSITIVE})["questions"][0]
        self.frq.approve_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        self.choose(None, key=[])
        saved = self.frq.settings()
        self.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"], "choice_reader": True}})
        self.reads, self.asked = [read("(b)")], []

    def choose(self, value, key=None):
        """Written work ({"written": True}) or the automatic rule (None), saved as the Key step saves it."""
        plan = self.frq.assignment(self.plan["id"])
        q = plan["questions"][0]
        return self.frq.save_answer_key(plan["id"], {"version": plan["version"], "questions": [{"question_id": self.qid, "version": q["version"],
            "answer_key": q["answer"]["key"] if key is None else key, "choice_grading": value}]})

    def grade_by_choice(self, failure=None):
        test = self

        class Reader:
            kind = "decisions"

            def cost(self, usage):
                return .00001, "scripted"

            def submit(self, request):
                test.asked.append(request)
                if failure:
                    raise failure
                return {"state": "completed", "result": {"output": {"model": "gpt-6-luna", "answers": test.reads.pop(0),
                        "usage": {"input_tokens": 100, "output_tokens": 0}}, "raw": {}, "metadata": {"usage": {"prompt_tokens": 100}}}}
        real = self.frq.cloud_provider
        self.addCleanup(patch.stopall)
        response, names = None, None
        with patch.object(self.frq, "cloud_provider", side_effect=lambda e, timeout=180: Reader() if e.get("kind") == "decisions" else real(e, timeout)):
            response, names = self.grade(CORRECT)
        return response, names

    def test_a_confident_read_of_the_key_is_the_grade_and_finalizes_by_itself(self):
        response, names = self.grade_by_choice()
        result, signals = response["result"], response["result"]["signals"]
        self.assertEqual(names, [])  # no reader, no grader: the read alone
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0]["payload"]["questions"]["marked"]["criteria"].keys() - {"none", "several"}, set(OPTIONS))
        self.assertEqual(([a["rubric_item_id"] for a in result["applications"] if a["applied"]], result["score"]["score"]), (["R1"], 10))
        self.assertEqual((result["state"], signals["risk"]["blocks"], signals["committee"]["status"]), ("proposed", [], "resolved"))
        self.assertEqual((signals["choice"]["choice"], signals["choice"]["orders_agree"], signals["answer"]["check"]), ("(b)", True, "match"))
        self.assertEqual(set(signals["risk"]["waived"]["choice_reader"]["blocks"]),
                         {risk.NOBODY_ELSE, "No independent OCR reading stands beside the vision reader."})
        self.assertEqual(signals["scope"]["choice_reader"], {"rule": choice.RULE, "model": "gpt-6-luna", "cutoff": choice.CUTOFF})
        self.assertEqual((response["acceptance"]["basis"], response["acceptance"]["kind"]), ("choice_reader", "automatic"))
        # The export says how it was finalized (requirement, 2026-11-06): not as grading-team agreement, no team having been convened.
        record = json.loads(self.frq.export(self.plan["id"])[0])["records"][0]
        self.assertEqual(record["final"]["basis"], "choice_reader")
        self.assertTrue(record["final"]["comment"].startswith("Finalized automatically after the multiple-choice reader:"), record["final"]["comment"])
        self.assertNotIn("grading-team agreement", record["final"]["comment"])
        self.assertNotIn("grading-team agreement", self.frq.export(self.plan["id"], "csv")[0].decode())
        with connect(self.path) as db:
            call = db.execute("SELECT step,role,provider,state FROM frq_cloud_calls WHERE step='choice'").fetchone()
        self.assertEqual(tuple(call), ("choice", "choice_reader", "decisions", "completed"))

    def test_a_confident_other_letter_earns_nothing_and_finalizes_too(self):
        self.reads = [read("(c)")]
        response, _ = self.grade_by_choice()
        result = response["result"]
        self.assertEqual(([a["rubric_item_id"] for a in result["applications"] if a["applied"]], result["score"]["score"]), (["R2"], 0))
        self.assertEqual(response["acceptance"]["basis"], "choice_reader")

    def test_an_unsure_empty_crowded_or_failed_read_is_graded_as_written_work(self):
        from grading.providers.base import ProviderError
        for name, reads, failure in (("orders disagree", [read("(b)", "(c)")], None), ("low", [read("(b)", p=.5)], None),
                                     ("none", [read(choice.NONE)], None), ("several", [read(choice.SEVERAL)], None),
                                     ("failed", [], ProviderError("Rate limited (HTTP 400).", confirmed=True, fixable=False))):
            with self.subTest(name=name):
                self.reads, self.asked = reads, []
                self.host.requests.clear()
                response, names = self.grade_by_choice(failure)
                self.assertIn("reconcile", names)  # the ordinary path read and graded it
                self.assertNotIn("choice", response["result"]["signals"])
                self.assertNotEqual((response.get("acceptance") or {}).get("basis"), "choice_reader")
                with connect(self.path) as db:
                    kept = json.loads(db.execute("SELECT output_json FROM frq_steps WHERE stage='choice' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
                self.assertTrue(kept.get("failed") or not kept["confident"], kept)  # the read is kept beside the ordinary path
                self.reset_responses()

    def latest_progress(self):
        with connect(self.path) as db:
            jid = db.execute("SELECT id FROM frq_jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        return self.frq.job(jid)["progress"]

    def test_the_batch_card_says_how_the_reader_did(self):
        # GRADING MULTIPLE-CHOICE READER HEALTH (2026-10-09): catalog only, on the batch's progress, and only for a batch that read.
        from grading.providers.base import ProviderError
        self.grade_by_choice()
        self.assertEqual(self.latest_progress()["choice_reads"], {"by_choice": 1, "written": 0, "reasons": {}})
        self.reset_responses()
        self.reads = [read(choice.SEVERAL)]
        self.grade_by_choice()
        self.assertEqual(self.latest_progress()["choice_reads"], {"by_choice": 0, "written": 1, "reasons": {"several": 1}})
        self.reset_responses()
        self.grade_by_choice(ProviderError("Authentication failed (HTTP 401).", confirmed=True, status=401, fixable=False))
        reads = self.latest_progress()["choice_reads"]
        self.assertEqual({k: v for k, v in reads.items() if k != "failures"}, {"by_choice": 0, "written": 1, "reasons": {"failed": 1}})
        self.assertEqual(len(reads["failures"]), 1)
        self.assertTrue(reads["failures"][0]["reason"].startswith("Authentication failed (HTTP 401)."))
        self.assertEqual(reads["failures"][0]["responses"], 1)
        self.reset_responses()
        saved = self.frq.settings()
        self.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"], "choice_reader": False}})
        self.grade_by_choice()
        self.assertNotIn("choice_reads", self.latest_progress())  # a batch that never read by choice reads as before

    def reset_responses(self):
        """Start the next sub-case from an ungraded question (a fresh batch reads it again)."""
        with connect(self.path) as db:
            db.execute("UPDATE frq_responses SET current_result_id=NULL,current_review_id=NULL")
            db.commit()

    def test_the_setting_off_never_reads_by_choice(self):
        saved = self.frq.settings()
        self.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"], "choice_reader": False}})
        response, names = self.grade_by_choice()
        self.assertEqual((self.asked, "reconcile" in names), ([], True))
        self.assertNotIn("choice", response["result"]["signals"])

    def test_written_work_is_the_instructors_until_the_question_has_grades(self):
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        self.assertNotIn("choice_grading", q["content"])  # nothing was saved: the rule alone
        self.assertEqual((q["answer"]["choice"]["grading"]["correct"], q["answer"]["choice"]["written"], q["answer"]["choice"]["locked"]),
                         ("(b)", False, False))
        self.choose({"written": True})
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        self.assertEqual((q["answer"]["choice"]["grading"], q["answer"]["choice"]["written"]), (None, True))
        response, names = self.grade_by_choice()
        self.assertEqual((self.asked, "reconcile" in names), ([], True))  # written work: never read by choice
        with self.assertRaisesRegex(ValueError, "already has grades"):
            self.choose(None)
        self.assertTrue(self.frq.assignment(self.plan["id"])["questions"][0]["answer"]["choice"]["locked"])

    def test_a_saved_key_other_than_the_rubrics_holds_a_read_back(self):
        self.choose(None, key=[{"label": "", "accept": ["(c)"]}])  # a key that contradicts the rubric's correct choice
        response, _ = self.grade_by_choice()
        self.assertIn(risk.KEY_DIFFERS, response["result"]["signals"]["risk"]["blocks"])
        self.assertIsNone(response.get("acceptance"))

    def test_a_multiple_choice_read_waits_for_the_cloud_lane_never_the_gpu(self):
        job = {"mode": "nebius", "operation": "recognize", "tier": "cloud_standard"}
        settings = {**self.frq.settings()["settings"], "glm_enabled": True}
        self.assertEqual(self.frq.next_lane(job, settings, "recognizing", (), 1), "")
        self.assertEqual(self.frq.next_lane(job, settings, "recognizing", (), 1, choice_pending=True), "cloud")


if __name__ == "__main__":
    unittest.main()
