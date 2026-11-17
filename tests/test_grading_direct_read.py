"""Cloud Standard reads the paper itself (requirement, 2026-09-21): no GLM-OCR, no Mathpix, the tier's own reader reading once
in plain text and then rendering that reading into the ordinary contract.

The measurement behind it is in `grading/direct_read.py`. What these tests hold is the part that must not drift: the OCR stage
buys nothing, the reading request carries no JSON contract, and the reader's own pass NEVER answers a check that asks what an
independent reader saw - it is hidden from those checks, and they are waived by name on the record instead of being satisfied.
"""
import json

from grading import config, direct_read, risk
from rosters import connect
from fake_openai import DIRECT_READING, prompt_name
import test_grading_teams as teams
from test_grading_pipeline import CORRECT, Pipeline


def reading_script(name, body):
    """The free-text pass. `('raw', ...)` is the fake host's plain-text answer: no JSON anywhere in it."""
    return ("raw", DIRECT_READING, "stop") if name == "read" else None


class DirectReading(Pipeline):
    legacy_workflow = False
    opinions = teams.GradingTeams.opinions

    def setUp(self):
        super().setUp()
        self.direct(True)

    def direct(self, on):
        saved = self.frq.settings()
        self.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"],
            "direct_reading": on, "glm_enabled": not on, "mathpix_enabled": False}})

    def steps(self, response_id):
        with connect(self.path) as db:
            rows = db.execute("SELECT s.stage, s.output_json FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id WHERE t.response_id=?", (response_id,)).fetchall()
        return {row["stage"]: json.loads(row["output_json"]) for row in rows}

    def test_the_setting_is_off_by_default_and_belongs_to_cloud_standard(self):
        self.assertIs(config.validate({})["direct_reading"], False)
        with self.assertRaises(ValueError):
            config.validate({"direct_reading": "yes"})
        self.assertIs(direct_read.enabled({"direct_reading": True}, "cloud_standard"), True)
        # Any other tier, like an unticked box, changes nothing.
        self.assertIs(direct_read.enabled({"direct_reading": True}, "cloud_high_assurance"), False)
        self.assertIs(direct_read.enabled({"direct_reading": False}, "cloud_standard"), False)
        self.assertIs(direct_read.enabled({}, "cloud_standard"), False)

    def test_a_cloud_batch_with_no_ocr_is_refused_unless_this_mode_is_on(self):
        saved = self.frq.settings()
        self.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"],
            "direct_reading": False, "glm_enabled": False, "mathpix_enabled": False}})
        with self.assertRaises(ValueError):
            self.queue(mode="cloud_standard")
        self.direct(True)
        self.queue(mode="cloud_standard")  # The mode supplies the reading the guard was protecting.

    def test_the_reading_stage_buys_no_ocr_and_the_request_carries_no_json_contract(self):
        response, names = self.grade(CORRECT, script=reading_script)
        self.assertIn("read", names)
        self.assertEqual(response["result"]["state"], "proposed")
        sent = [r["body"] for r in self.host.requests if r["path"].endswith("/chat/completions") and prompt_name(r["body"]) == "read"]
        self.assertTrue(sent)
        for body in sent:
            # A text prompt: nothing decodes the answer, nothing describes a schema, and no host is required to support one.
            self.assertNotIn("response_format", body)
            self.assertNotIn("Output JSON schema", body["messages"][0]["content"])
            self.assertNotIn("require_parameters", body.get("provider", {}))
        reading = self.steps(response["id"])["recognizing"]
        self.assertEqual([c["provider"] for c in reading["candidates"]], [direct_read.PROVIDER])
        self.assertEqual(reading["direct_reading"], direct_read.RULE)
        self.assertIn("x = 6", reading["candidates"][0]["transcript"]["plain_text"])
        # Nothing was aligned, because one reading cannot agree with itself.
        self.assertEqual((reading["consensus"]["providers"], reading["consensus"]["agreement"]), (0, None))

    def test_the_readers_own_pass_never_answers_a_check_about_an_independent_reader(self):
        response, _ = self.grade(CORRECT, script=reading_script)
        signals = response["result"]["signals"]
        components = signals["risk"]["components"]
        # It is not counted as a provider, and the check that asks for one is recorded as removed, not as passed.
        self.assertEqual((components["ocr"]["providers"], components["ocr"]["direct"]), (0, direct_read.RULE))
        self.assertNotIn("No independent OCR reading stands beside the vision reader.", signals["risk"]["blocks"])
        self.assertEqual(signals["risk"]["waived"]["direct_reading"]["rule"], direct_read.RULE)
        # EXACTLY the checks about a second reader, and nothing else, lose their power to hold the response.
        self.assertEqual(signals["risk"]["waived"]["direct_reading"]["blocks"],
                         ["No independent OCR reading stands beside the vision reader."])
        # With no second reading there is nothing two readers could differ on: None, never False.
        self.assertIsNone(components["answer"]["readers_agree"])
        self.assertNotIn("The final answer rests on one reading.", signals["risk"]["blocks"])
        self.assertEqual(response["result"]["state"], "proposed")

    def test_the_graders_are_shown_no_second_opinion_that_does_not_exist(self):
        _, names = self.grade(CORRECT, script=reading_script)
        graded = [r["body"] for r in self.host.requests if r["path"].endswith("/chat/completions") and prompt_name(r["body"]) == "grade"]
        context = json.loads(graded[0]["messages"][1]["content"][0]["text"])["reading_context"]
        self.assertEqual((context["ocr_readings"], context["ocr_disagreements"], context["visual_vs_ocr"]), ([], [], []))

    def test_the_mode_and_its_prompt_are_part_of_the_acceptance_scope(self):
        response, _ = self.grade(CORRECT, script=reading_script)
        scope = response["result"]["signals"]["scope"]
        self.assertEqual(scope["direct_reading"], direct_read.RULE)
        self.assertIn(direct_read.PROMPT, scope["prompts"])
        # A batch with the OCR reading keeps the scope, and the permissions, it always had.
        self.direct(False)
        again, _ = self.grade(CORRECT)
        plain = again["result"]["signals"]["scope"]
        self.assertNotIn("direct_reading", plain)
        self.assertNotIn(direct_read.PROMPT, plain["prompts"])
        self.assertNotEqual(response["result"]["signals"]["scope_sha256"], again["result"]["signals"]["scope_sha256"])

    def test_an_empty_reading_is_a_blank_answer_to_look_at_and_not_a_failure(self):
        """requirement, 2026-09-22: an unanswered question must reach the instructor, not kill the response."""
        empty = lambda *a, **k: {"output": {"text": "   "}, "raw": {}, "metadata": {}}
        reading = direct_read.read(empty, {"name": "alpha-vision"}, b"image", {"direct_reading_rule": direct_read.RULE}, {"step": "ocr:0:direct"})
        self.assertEqual((reading["transcript"]["lines"], reading["raw"]), ([], {"rule": direct_read.RULE, "empty": True}))
        self.assertEqual(direct_read.blank([{"raw": {"rule": direct_read.RULE}}, reading, {}]), [1])
        # A batch from before the key can be RETRIED under the current rule and finish, because v1's answer to an empty
        # reading was a failed component and never a grade: nothing an older batch produced depends on it.
        self.assertEqual(direct_read.read(empty, {"name": "alpha-vision"}, b"image", {}, {"step": "ocr:0:direct"})["raw"]["rule"], direct_read.RULE)
        self.assertEqual((direct_read.rule({}), direct_read.rule({"direct_reading_rule": "direct-reading-v9"})),
                         (direct_read.RULE, "direct-reading-v9"))  # A frozen rule still wins, so a later one leaves v2 alone.

    def test_a_blank_answer_is_graded_but_waits_for_the_instructor(self):
        """The response is created, the team grades the empty transcript, and the reading reason keeps it out of acceptance."""
        blank = lambda name, body: ("raw", "   ", "stop") if name == "read" else None
        response, names = self.grade(CORRECT, script=blank)
        self.assertIn("read", names)
        self.assertIn("grade", names)  # It goes on to the graders; nothing failed.
        result = response["result"]
        # A reading in doubt: ocr_review, or `both` when the grade is doubted too (here a grader claimed credit for work
        # that is not in the transcript). Either way it is one of the flagged states the review queue lists.
        self.assertIn(result["state"], ("ocr_review", "both"))
        self.assertIsNone(response["acceptance"])  # Never finalized automatically while the reading is in doubt.
        self.assertTrue(any("no handwriting" in reason for reason in result["reasons"]), result["reasons"])
        reading = self.steps(response["id"])["recognizing"]
        self.assertEqual(reading["candidates"][0]["blank_crops"], [0])
        self.assertEqual(reading["candidates"][0]["transcript"]["lines"], [])

    def test_a_blank_answer_jev_also_sees_left_blank_is_finalized_and_any_doubt_waits(self):
        """JEV BLANK ANSWERS (requirement, 2026-10-15): where everything holding the zero follows from the
        empty reading (`jev_blank.eligible`) and Jev says blank at .8 or above (.9 until 2026-10-31), the zero goes to the ordinary acceptance gate."""
        from unittest.mock import patch
        from grading import arbitration, jev_blank
        blank = lambda name, body: ("raw", "   ", "stop") if name == "read" else None
        class Jev:
            def __init__(self):
                self.calls = []
                self.noul = .95

            def ask(self, purpose, owner, state, questions, rule, **kwargs):
                self.calls.append((purpose, owner, len(kwargs["images"])))
                return {"id": "synthetic-blank-check", "status": "complete", "answers": {"blank": {"noul": self.noul}}}

        self.frq.jev = jev = Jev()
        # Pipeline.grade resets provider patches before work starts. Keep only availability
        # faked during execution: the real eligibility, image check and acceptance must run.
        run_all = self.run_all
        def run_with_jev():
            with patch("jev.client.available", return_value=True):
                run_all()
        self.run_all = run_with_jev

        for self.noul, finalized in ((.62, False), (.95, True)):
            with self.subTest(noul=self.noul):
                jev.noul = self.noul
                response, _ = self.grade([], primary=("R4",), script=blank)
                result = response["result"]
                self.assertEqual(result["signals"]["jev_blank"]["noul"], self.noul)
                if finalized:
                    self.assertEqual((result["state"], result["signals"]["jev_blank"]["outcome"]), ("proposed", "blank"))
                    self.assertTrue(any("no handwriting" in r for r in result["signals"]["jev_blank"]["settled"]["reasons"]))
                    self.assertEqual(arbitration.basis(result["signals"]), "jev_blank")
                    self.assertEqual((response["acceptance"] or {}).get("basis"), "jev_blank", response["acceptance"])
                else:
                    self.assertIn(result["state"], ("ocr_review", "both"))
                    self.assertIsNone(response["acceptance"])
        self.assertEqual([call[0] for call in jev.calls], [jev_blank.PURPOSE] * 2)
        self.assertTrue(all(call[2] == 1 for call in jev.calls))
        # Not eligible (here the graders gave credit beside the empty reading): Jev is never asked.
        jev.calls.clear()
        response, _ = self.grade(CORRECT, script=blank)
        self.assertEqual((jev.calls, response["result"]["signals"].get("jev_blank")), ([], None))

        # A real reading concern is not the automatic empty-transcript warning.
        from fake_openai import reconcile_answer
        def faint_mark(payload):
            result = reconcile_answer(payload)
            result["transcript"]["ambiguities"].append("A faint pencil mark might be an answer.")
            return result
        response, _ = self.grade([], primary=("R4",), script=blank, reader=faint_mark)
        self.assertIsNone(response["acceptance"])
        self.assertIn(risk.HANDWRITING, response["result"]["signals"]["risk"]["blocks"])
        self.assertEqual((jev.calls, response["result"]["signals"].get("jev_blank")), ([], None))

    def test_the_candidate_is_hidden_from_independent_evidence_but_kept_on_the_record(self):
        candidates = [{"provider": "mathpix"}, {"provider": direct_read.PROVIDER}]
        self.assertEqual(direct_read.independent(candidates), [{"provider": "mathpix"}])
        self.assertEqual(direct_read.independent([{"provider": direct_read.PROVIDER}]), [])
        self.assertIs(direct_read.waived({"ocr": {"direct": direct_read.RULE}}), True)
        self.assertIs(direct_read.waived({"ocr": {}}), False)


def arranging(change_again):
    """The reconcile call rewriting x = 6 as x = 8, a number the free reading does not have; asked again, it copies the free reading
    unless `change_again`."""
    import copy
    from fake_openai import payload_of
    def script(name, body):
        if name == "read":
            return ("raw", DIRECT_READING, "stop")
        if name == "reconcile":
            payload = payload_of(body)
            arranged = copy.deepcopy(payload["candidates"][0]["transcript"])
            if "introduced_numbers" not in payload or change_again:
                element = arranged["lines"][-1]["elements"][0]
                element["text"] = element["latex"] = "x = 8"
            return {"transcript": arranged, "visual_confidence": .96}
        return None
    return script


class FirstReadingNumbers(Pipeline):
    """direct-reading-v3 (requirement, 2026-09-29): the reconcile call
    arranges the reader's own free reading and keeps its numbers; a number it introduces is asked about once more, then held."""
    legacy_workflow = False
    opinions = teams.GradingTeams.opinions
    direct, steps = DirectReading.direct, DirectReading.steps

    def queue(self, *args, **kwargs):
        from unittest.mock import patch
        # Exercise retained v3 batches; the v4 contract has separate regression coverage.
        with patch.object(direct_read, "RULE", "direct-reading-v3" if direct_read.RULE == "direct-reading-v4" else direct_read.RULE):
            return super().queue(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.direct(True)

    def test_numbers_are_compared_as_written_not_as_formatted(self):
        from grading.models import plain_transcript
        for first, arranged, expected in (("B = (0,100)\nC = (12,4)", "B = (0,10)\nC = (12,4)", (["10"], ["100"])),
                                          ("P = 17,600", "P = 17600", ([], [])), ("P = 17600", "P = 17,600", ([], [])),
                                          ("x = [crossed out: 17] 12", "x = 17 12", ([], [])), ("y = [illegible]", "y = 7", (["7"], [])),
                                          ("3x=18\nx=6", "x=6\n3x=18", ([], [])), ("x = 8", "x = 6", (["6"], ["8"])),
                                          ("[marked: (2)]\n[crossed out: (3)]", "(2)\n(3)", ([], []))):
            with self.subTest(first=first):
                self.assertEqual(direct_read.introduced(first, plain_transcript(arranged)), expected)
        self.assertTrue(direct_read.keeps_numbers({}))
        self.assertFalse(direct_read.keeps_numbers({"direct_reading_rule": "direct-reading-v2"}))  # a v2 batch finishes as it ran

    def test_a_marked_printed_choice_is_read_as_its_label_never_a_bare_circle(self):
        """read-v3 / reconcile-v9 (requirement, 2026-09-29): the audit grouped papers that circled different choices as the same answer
        "○", because the reader wrote the mark instead of the choice."""
        from grading.providers.local_vlm import prompt
        read, arrange = prompt("read")[0], prompt("reconcile")[0]
        self.assertIn("[marked: (b)]", read)
        self.assertIn("Never write the circle, box, tick or slash itself", read)
        self.assertIn("[marked: ...] is a printed choice", arrange)
        self.assertIn("never as a circle, box, slash or tick", arrange)

    def test_an_introduced_number_is_asked_about_again_and_the_first_readings_number_kept(self):
        response, names = self.grade(CORRECT, script=arranging(False))
        self.assertEqual(names.count("reconcile"), 2)
        from fake_openai import payload_of
        again = [payload_of(r["body"]) for r in self.host.requests if r["path"].endswith("/chat/completions") and prompt_name(r["body"]) == "reconcile"][1]
        self.assertEqual(again["introduced_numbers"], ["8"])
        check = self.steps(response["id"])["reconciling"]["first_reading_check"]
        self.assertEqual(check, {"rule": "direct-reading-v3", "introduced": ["8"], "missing": ["6"], "asked_again": True, "still": [], "still_missing": []})
        self.assertEqual(response["result"]["transcript"]["lines"][-1]["elements"][0]["text"], "x = 6")
        self.assertEqual(response["result"]["state"], "proposed")

    def test_a_number_the_reader_keeps_introducing_holds_the_response_with_both_readings(self):
        response, _ = self.grade(CORRECT, script=arranging(True))
        check = self.steps(response["id"])["reconciling"]["first_reading_check"]
        self.assertEqual((check["still"], check["still_missing"]), (["8"], ["6"]))
        doubt = direct_read.doubt(["8"], ["6"])
        self.assertIn(doubt, response["result"]["transcript"]["ambiguities"])
        self.assertIn(doubt, response["result"]["reasons"])
        self.assertNotEqual(response["result"]["state"], "proposed")
        self.assertIsNone(response["acceptance"])

    def test_a_batch_frozen_before_the_rule_is_not_asked_again(self):
        from unittest.mock import patch
        with patch.object(direct_read, "RULE", "direct-reading-v2"):
            response, names = self.grade(CORRECT, script=arranging(True))
        self.assertEqual(names.count("reconcile"), 1)
        self.assertNotIn("first_reading_check", self.steps(response["id"])["reconciling"])


class DirectReadingPolicy(Pipeline):
    """The waiver is exactly two checks wide: everything else still holds a response back."""
    legacy_workflow = False
    opinions = teams.GradingTeams.opinions

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        self.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"],
            "direct_reading": True, "glm_enabled": False, "mathpix_enabled": False}})

    def test_a_team_that_does_not_agree_still_waits_for_the_instructor(self):
        def script(name, body):
            return reading_script(name, body) or self.opinions(**{"alpha-vision": ("R2",), "beta-text": ("R1",)})(name, body)
        response, _ = self.grade(CORRECT, script=script)
        self.assertEqual((response["result"]["state"], response["acceptance"]), ("grading_review", None))
        self.assertTrue(response["result"]["signals"]["risk"]["blocks"])

    def test_the_ordinary_checks_still_hold_a_response(self):
        """A wrong reading is still caught: the waiver is about a missing second reader, not about the grade."""
        from test_grading_pipeline import INCONSISTENT
        response, _ = self.grade(INCONSISTENT, primary=("R1",), script=lambda name, body:
                                 ("raw", "3x - 8 = 10\n3x = 10 + 8\n3x = 18\nx = 8", "stop") if name == "read" else None)
        self.assertEqual((response["result"]["state"], response["acceptance"]), ("grading_review", None))
        self.assertTrue(response["result"]["signals"]["risk"]["blocks"])
