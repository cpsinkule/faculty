import io
import json
import os
import unittest
from unittest.mock import patch

from grading import config, extraction, recognition, rubrics
from grading.models import plain_transcript, transcript
from grading.providers.base import ProviderError
from grading.providers.glm_ocr import GlmOcrProvider
from grading.providers.local_vlm import LocalVlmProvider
from grading.providers.mathpix import MathpixProvider
from grading.metrics import report
from frq_fixtures import CASES, page


class ProviderContracts(unittest.TestCase):
    def test_glm_adapter_keeps_written_error_and_unknown_confidence(self):
        with patch("faculty_ocr.recognize", return_value={"text": "x = 8", "model": "synthetic-glm", "truncated": False}) as mocked:
            result = GlmOcrProvider().recognize(b"synthetic")
        self.assertEqual(result["transcript"]["plain_text"], "x = 8")
        self.assertIsNone(result["transcript"]["confidence"])
        self.assertEqual(result["metadata"]["model"], "synthetic-glm")
        mocked.assert_called_once_with(b"synthetic", task="text")

    def test_a_busy_mathpix_is_a_capacity_failure_and_any_other_error_names_only_its_identifier(self):
        from grading.providers.base import ProviderError
        with patch.dict(os.environ, {"MATHPIX_APP_ID": "fixture-id", "MATHPIX_APP_KEY": "fixture-secret"}):
            for ident, status in (("http_max_requests", 429), ("sys_exception", 503), ("image_no_content", None)):
                with patch("grading.providers.mathpix.request_json", return_value={"error": "details that may quote the image", "error_info": {"id": ident, "message": "private"}}):
                    with self.assertRaises(ProviderError) as caught:
                        MathpixProvider().recognize(b"png")
                self.assertEqual((caught.exception.status, caught.exception.confirmed, caught.exception.confirmation["mathpix_error"]), (status, True, ident))
                self.assertNotIn("private", json.dumps(caught.exception.confirmation) + str(caught.exception))

    def test_mathpix_geometry_credentials_and_no_training_request(self):
        raw = {"text": "x = 8", "confidence": .9, "image_width": 100, "image_height": 200,
            "line_data": [{"text": "x = 8", "cnt": [[10,20],[80,20],[80,60],[10,60]], "confidence": .8}], "request_id": "opaque"}
        with patch.dict(os.environ, {"MATHPIX_APP_ID": "fixture-id", "MATHPIX_APP_KEY": "fixture-secret"}), patch("grading.providers.mathpix.request_json", return_value=raw) as transport:
            result = MathpixProvider().recognize(b"image")
        self.assertEqual(result["transcript"]["lines"][0]["elements"][0]["bbox"], [.1,.1,.8,.3])
        sent = transport.call_args.kwargs
        self.assertFalse(sent["body"]["metadata"]["improve_mathpix"])
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertEqual(sent["headers"]["app_key"], "fixture-secret")

    def test_the_pages_latex_is_the_page_and_never_the_first_formula_snippet(self):
        """Measured 2026-09-21: `data[0]` is ONE detected formula, so the page's `latex` became a single symbol beside 528
        characters of text on 201 of 240 responses. `consensus.align` prefers `latex`, so every reader-versus-OCR comparison
        diffed the real reading against that stub and reported the whole reading as one difference, on every response."""
        raw = {"text": "18. Assuming \\( G \\) is a \\( 2 \\times 5 \\) matrix\nx = 8", "confidence": .9,
               "data": [{"type": "latex", "value": "G"}],   # one snippet of the printed question
               "image_width": 100, "image_height": 200,
               "line_data": [{"text": "x = 8", "data": [{"type": "latex", "value": "x = 8"}], "cnt": [[10,20],[80,20],[80,60],[10,60]], "confidence": .8}]}
        with patch.dict(os.environ, {"MATHPIX_APP_ID": "fixture-id", "MATHPIX_APP_KEY": "fixture-secret"}), patch("grading.providers.mathpix.request_json", return_value=raw):
            result = MathpixProvider().recognize(b"image")
        page = result["transcript"]
        self.assertEqual(page["latex"], raw["text"])
        self.assertNotEqual(page["latex"], "G")
        self.assertEqual(page["lines"][0]["elements"][0]["latex"], "x = 8")  # a LINE's own latex still comes from its own data.

    def test_a_stub_page_latex_can_never_decide_a_reading_comparison(self):
        """The defensive half: a retained reading already holds the stub, and it must not stand in for the whole reading."""
        from grading import consensus
        from grading.models import transcript as build
        def reading(text, latex):
            return build({"plain_text": text, "latex": latex,
                          "lines": [{"id": f"l{n}", "elements": [{"type": "text", "text": part, "latex": part, "bbox": [0, .1 * n, 1, .1 * n + .05]}]}
                                    for n, part in enumerate(text.split("\n"), 1)]})
        real = reading("x = 8\ny = 2", "x = 8\ny = 2")
        stubbed = reading("x = 8\ny = 2", "G")          # same reading, but the page latex is a one-symbol stub
        self.assertEqual(consensus.comparable(stubbed), consensus.comparable(real))
        pair = consensus.align([{"provider": "visual_reader", "transcript": real}, {"provider": "mathpix", "transcript": stubbed}])
        self.assertEqual((pair["disagreements"], pair["agreement"]), ([], 1.0))  # Identical readings: nothing to report.
        differs = consensus.align([{"provider": "visual_reader", "transcript": reading("x = 9\ny = 2", "x = 9\ny = 2")},
                                   {"provider": "mathpix", "transcript": stubbed}])
        self.assertEqual([("".join(d["first"]), "".join(d["second"])) for d in differs["disagreements"]], [("9", "8")])  # Only the real difference.
        self.assertTrue(differs["disagreements"][0]["locations"])  # Element-derived tokens line up, so the location is exact.

    def test_nebius_documented_transport_and_prompt_metadata(self):
        raw = {"model": "actual-model", "choices": [{"finish_reason": "stop", "message": {"content": '{"applications":[]}'}}], "usage": {"total_tokens": 123}}
        with patch.dict(os.environ, {"NEBIUS_API_KEY": "fixture-secret", "NEBIUS_GRADING_URL": "https://example.invalid/v1"}), patch("grading.providers.local_vlm.request_json", return_value=raw) as transport:
            provider = LocalVlmProvider(config.validate({}), "nebius")
            result = provider.grade({"response_id": "opaque", "problem": "Solve"}, [b"image"])
        sent = transport.call_args.kwargs
        self.assertEqual(transport.call_args.args[0], "https://example.invalid/v1/chat/completions")
        self.assertFalse(sent["body"]["store"])
        self.assertEqual(sent["body"]["temperature"], 0)
        self.assertEqual(result["metadata"]["prompt"], "grade-v8.txt")  # v3 separated the mathematical interpretation from the rubric; it awards nothing.
        self.assertEqual(result["metadata"]["reported_model"], "actual-model")
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_malformed_truncated_nonfinite_output_retains_private_receipt(self):
        for content, stop in (("not json", "stop"), ('{"x":NaN}', "stop"), ('{}', "length")):
            raw = {"choices": [{"finish_reason": stop, "message": {"content": content}}]}
            with self.subTest(content=content), patch.dict(os.environ, {"FACULTY_GRADING_VERIFIER_URL": "http://127.0.0.1:8000/v1"}), patch("grading.providers.local_vlm.request_json", return_value=raw):
                with self.assertRaises(ProviderError) as raised:
                    LocalVlmProvider(config.validate({})).verify({}, [])
                self.assertEqual(raised.exception.raw, raw)
                self.assertNotIn(content, str(raised.exception))

    def test_local_endpoint_refuses_cloud_and_embedded_credentials(self):
        for url in ("https://example.com/v1", "http://user:secret@localhost/v1", "http://localhost/v1?key=secret"):
            with patch.dict(os.environ, {"FACULTY_GRADING_VERIFIER_URL": url}), self.assertRaises(ValueError):
                config.endpoint("local_verifier")
        with patch.dict(os.environ, {"NEBIUS_GRADING_URL": "http://example.com/v1"}), self.assertRaises(ValueError):
            config.endpoint("nebius")

    def test_native_ollama_releases_gpu_and_rejects_remote_models(self):
        settings = config.validate({"local_verifier_backend":"ollama"})
        info = {"details":{"quantization_level":"Q4_K_M"},"capabilities":["vision"]}
        raw = {"done":True,"done_reason":"stop","message":{"content":"{}"},"prompt_eval_count":2,"eval_count":3}
        with patch.dict(os.environ, {"FACULTY_GRADING_VERIFIER_URL":"http://127.0.0.1:11434/v1"}), patch("grading.providers.local_vlm.request_json", side_effect=[info,raw]) as transport:
            result = LocalVlmProvider(settings).verify({}, [b"image"])
            self.assertEqual(transport.call_args.kwargs["body"]["keep_alive"], 0)
            self.assertIn("transcript", transport.call_args.kwargs["body"]["format"]["properties"])
            self.assertEqual(transport.call_args.kwargs["body"]["format"]["properties"]["transcript"]["properties"]["lines"]["items"]["properties"]["elements"]["items"]["properties"]["crop"], {"enum":[0]})
            self.assertEqual(result["metadata"]["usage"]["total_tokens"], 5)
        with patch.dict(os.environ, {"FACULTY_GRADING_VERIFIER_URL":"http://127.0.0.1:11434/v1"}), patch("grading.providers.local_vlm.request_json", side_effect=[info,raw]) as transport:
            LocalVlmProvider(settings).grade({"rubric":{"items":[{"id":"R1"},{"id":"R2"}]}}, [b"image"])
            body = transport.call_args.kwargs["body"]
            applications = body["format"]["properties"]["applications"]
            self.assertEqual((applications["minItems"], applications["maxItems"]), (2,2))
            self.assertEqual(applications["items"]["properties"]["rubric_item_id"]["enum"], ["R1","R2"])
            self.assertEqual(applications["items"]["properties"]["evidence"]["items"]["properties"]["crop"], {"enum":[0]})
            self.assertIn('"maxItems":2', body["messages"][0]["content"])
        with patch.dict(os.environ, {"FACULTY_GRADING_VERIFIER_URL":"http://127.0.0.1:11434/v1"}), patch("grading.providers.local_vlm.request_json", return_value={**info,"remote_host":"cloud"}) as transport:
            with self.assertRaises(ProviderError):
                LocalVlmProvider(settings).verify({}, [b"image"])
            self.assertEqual(transport.call_count, 1)


    def test_a_local_model_request_borrows_the_whole_gpu_and_a_cloud_one_does_not(self):
        # EMBEDDING "LENDS THE GPU" (requirement, 2026-10-11): the embedding worker and the local model
        # do not fit on the card together, so the worker moves off it for exactly the request and is told when it is over.
        settings = config.validate({"local_verifier_backend": "ollama", "timeout": 120})
        info = {"details": {"quantization_level": "Q4_K_M"}, "capabilities": ["vision"]}
        raw = {"done": True, "done_reason": "stop", "message": {"content": "{}"}, "prompt_eval_count": 2, "eval_count": 3}
        events, answers = [], []

        def worker(payload, timeout=None, path=None):
            events.append(payload["op"])
            return {"lent": payload["op"] == "lend"}

        def host(url, **sent):
            events.append(url.rsplit("/", 1)[-1])
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        local = {"FACULTY_GRADING_VERIFIER_URL": "http://127.0.0.1:11434/v1", "FACULTY_GRADING_GRADER_URL": "http://127.0.0.1:8000/v1"}
        named = {**local, "FACULTY_EMBEDDING_SOCKET": "/named/embedding.sock"}
        with patch.dict(os.environ, named), patch("faculty_embedding.client.ask", side_effect=worker) as asked, \
                patch("grading.providers.local_vlm.request_json", side_effect=host):
            answers[:] = [info, raw]
            LocalVlmProvider(settings).locate({}, [b"image"])
            self.assertEqual(events, ["show", "lend", "chat", "take_back"])  # asking which model is installed loads nothing
            self.assertEqual(asked.call_args_list[0].args[0], {"op": "lend", "seconds": 150})  # the request's own limit and a little more
            events.clear()
            answers[:] = [info, ProviderError("Endpoint request failed (HTTP 500).")]
            with self.assertRaises(ProviderError):
                LocalVlmProvider(settings).locate({}, [b"image"])
            self.assertEqual(events, ["show", "lend", "chat", "take_back"])  # a failed request returns the card too
            events.clear()
            answers[:] = [{"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}]
            LocalVlmProvider(settings, "local_grader").grade({"rubric": {"items": []}}, [b"image"])  # any local server, not only Ollama
            self.assertEqual(events, ["lend", "completions", "take_back"])
        events.clear()
        with patch.dict(os.environ, local), patch("faculty_embedding.client.ask", side_effect=AssertionError("never asked")), \
                patch("grading.providers.local_vlm.request_json", side_effect=host):
            os.environ.pop("FACULTY_EMBEDDING_SOCKET", None)
            answers[:] = [info, raw]
            LocalVlmProvider(settings).locate({}, [b"image"])  # no worker named (a check, a script): the request goes as before
        self.assertEqual(events, ["show", "chat"])
        events.clear()
        cloud = {"choices": [{"finish_reason": "stop", "message": {"content": '{"applications":[]}'}}]}
        with patch.dict(os.environ, {**named, "NEBIUS_API_KEY": "fixture-secret", "NEBIUS_GRADING_URL": "https://example.invalid/v1"}), \
                patch("faculty_embedding.client.ask", side_effect=AssertionError("never asked")), \
                patch("grading.providers.local_vlm.request_json", side_effect=host):
            answers[:] = [cloud]
            LocalVlmProvider(config.validate({}), "nebius").grade({"response_id": "opaque", "problem": "Solve"}, [b"image"])
        self.assertEqual(events, ["completions"])


class ExtractionContracts(unittest.TestCase):
    def test_registration_preserves_original_pixels_and_cloud_mask(self):
        from PIL import Image
        original = page(messy=True)
        result = extraction._prepare(original, {"x":.1,"y":.2,"width":.8,"height":.5}, page(template=True), [[0,0,.2,.2]])
        self.assertTrue(result["metadata"]["registered"])
        crop = Image.open(io.BytesIO(result["images"]["original"]))
        expected = Image.open(io.BytesIO(original)).crop(result["metadata"]["crop_bounds"])
        self.assertEqual(crop.tobytes(), expected.tobytes())
        cloud = Image.open(io.BytesIO(result["images"]["cloud"]))
        self.assertEqual(cloud.getpixel((1,1)), (255,255,255))
        self.assertEqual(set(result["images"]), {"original","grayscale","contrast","ink","cloud"})

    def test_missing_template_and_corrupt_image_never_certify_registration(self):
        result = extraction._prepare(page(), {"x":.1,"y":.2,"width":.8,"height":.5})
        self.assertFalse(result["metadata"]["registered"])
        with self.assertRaises(Exception):
            extraction._prepare(b"corrupt", {"x":0,"y":0,"width":1,"height":1})

    def test_a_scan_that_does_not_align_with_its_blank_exam_is_reported_as_uncertain_not_a_crash(self):
        import io as _io, json as _json
        from PIL import Image, ImageDraw
        other = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(other)
        for index in range(12):  # A different sheet altogether: diagonal strokes where the question box should be.
            draw.line((40 + index * 60, 60, 760 - index * 40, 940), fill="black", width=5)
        data = _io.BytesIO(); other.save(data, "PNG")
        result = extraction._prepare(page(), {"x": .1, "y": .2, "width": .8, "height": .5}, data.getvalue())
        self.assertIs(result["metadata"]["registered"], False)
        self.assertIn("alignment is uncertain", result["metadata"]["reason"])
        _json.dumps(result["metadata"])  # What the sandbox writes; a NumPy boolean here failed the whole response.

    def test_actual_bounded_document_sandbox(self):
        result = extraction.extract(page(), {"x":.1,"y":.2,"width":.8,"height":.5}, page(template=True))
        self.assertTrue(result["images"]["original"].startswith(b"\x89PNG"))
        self.assertTrue(result["metadata"]["registered"])


class ModelValidationContracts(unittest.TestCase):
    def test_exact_edit_distance_for_text_and_math_tokens(self):
        from itertools import product
        from grading.metrics import distance
        def reference(left, right):
            previous = list(range(len(right) + 1))
            for i, first in enumerate(left, 1):
                current = [i]
                for j, second in enumerate(right, 1):
                    current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (first != second)))
                previous = current
            return previous[-1]
        words = ["".join(p) for n in range(5) for p in product("ab", repeat=n)]
        for left in words:
            for right in words:
                self.assertEqual(distance(left, right), reference(left, right))
        self.assertEqual(distance(["x","^","2","+","1"], ["x","^","2","-","1"]), 1)
        self.assertEqual(distance("x" * 20000, "x" * 9999 + "-" + "x" * 10000), 1)

    def test_unknown_rules_and_fractional_geometry_indices_are_refused(self):
        with self.assertRaises(ValueError):
            rubrics.validate({"items": [{"id":"R1","description":"Error","points":-1,"invented_rule":True}]}, 5)
        value = plain_transcript("x = 8")
        value["lines"][0]["elements"][0]["crop"] = .5
        with self.assertRaises(ValueError):
            transcript(value)

    def test_gradescope_evaluations_and_original_copy(self):
        source = "Name,Error A,Error B\nSynthetic,1,0\nRubric Numbers,1.1,1.2\nPoint Values,-1,-2\n"
        rules = rubrics.parse(source, 10, "csv")
        self.assertEqual([i["points"] for i in rules["items"]], [-1,-2])
        self.assertTrue(rules["warnings"])

    def test_a_ragged_generic_csv_is_refused_as_a_value_error_not_a_server_error(self):
        for source in ("id,description,points\nR1,Correct\n", "id,description,points\nR1,Correct,-1,extra\n",
                       "id,description,points\nR1,Correct,-1,\n", "points,id,description\n-1,R1\n"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                rubrics.parse(source, 5, "csv")
        # A short row missing only its last cell, the id, still takes the generated one.
        rules = rubrics.parse("description,points,id\nCorrect,-1\n", 5, "csv")
        self.assertEqual([i["id"] for i in rules["items"]], ["R1"])

    def test_changed_imported_points_keep_semantics_but_meaning_changes_do_not(self):
        initial = rubrics.parse("-1 Distribution error", 10)
        points = rubrics.parse("-0.5 Distribution error", 10)
        meaning = rubrics.parse("-0.5 Distribution or sign error", 10)
        self.assertEqual(rubrics.semantic_hash(initial), rubrics.semantic_hash(points))
        self.assertNotEqual(rubrics.semantic_hash(initial), rubrics.semantic_hash(meaning))

    def test_fixtures_keep_arithmetic_errors_and_symbol_structure(self):
        for name, content in CASES:
            with self.subTest(name=name):
                self.assertEqual(plain_transcript(content)["plain_text"], content)

    def test_unsupported_evidence_requires_review_and_unknown_items_fail(self):
        import test_frq_grading as fixture
        raw = fixture.FakeProvider().grade({}, [b"image"])["output"]
        rules = rubrics.parse("-1 Error", 10)
        raw["applications"][0]["evidence"][0]["student_text"] = "x = 5"
        parsed = recognition.validate_grade(raw, plain_transcript("3x - 8 = 10"), rules, 10, 1)
        self.assertTrue(parsed["review_reasons"])
        self.assertEqual(parsed["applications"][0]["evidence"], [])
        raw["applications"][0]["rubric_item_id"] = "invented"
        with self.assertRaises(ValueError):
            recognition.validate_grade(raw, plain_transcript("x = 8"), rules, 10, 1)

    def test_a_quotation_is_found_across_line_breaks_and_spellings_but_never_when_a_character_differs(self):
        # From the first real batch: a third of responses were flagged "evidence incomplete" for quotations that WERE in the transcript.
        from grading.models import transcript
        reading = transcript({"plain_text": "", "latex": "", "confidence": .9, "ambiguities": [], "lines": [
            {"id": "line-1", "elements": [{"type": "text", "text": "When y decreases by 21 units, x", "latex": "", "bbox": None, "crop": 0, "confidence": .9},
                                           {"type": "text", "text": "decreases", "latex": "", "bbox": None, "crop": 0, "confidence": .9},
                                           {"type": "math", "text": "168/11", "latex": "\\frac{168}{11}", "bbox": None, "crop": 0, "confidence": .9}]}]})
        rules = rubrics.validate({"mode": "positive", "items": [{"id": "R1", "description": "Correct answers", "points": 2}, {"id": "R2", "description": "No Answer or Completely Incorrect", "points": 0}]}, 2)
        def grade(quote, item="R1", evidence=True):
            chosen = [{"line_id": "line-1", "student_text": quote, "bbox": None, "crop": 0}] if evidence else []
            raw = {"applications": [{"rubric_item_id": i, "applied": i == item, "reason": "", "evidence": chosen if i == item else [], "origin_error_id": None, "confidence": .95, "ambiguity": None} for i in ("R1", "R2")],
                   "interpretation": {"error_graph": [], "final_answers": []}, "confidence": .95, "rubric_ambiguities": [], "review_reasons": []}
            return recognition.validate_grade(raw, reading, rules, 2, 1)["review_reasons"]
        self.assertEqual(grade("x decreases 168/11"), [])                 # across the elements of one line
        self.assertEqual(grade("$\\frac{168}{11}$"), [])                   # the LaTeX spelling of the same line, wrapped in $
        self.assertEqual(len(grade("x decreases 168/17")), 2)             # one character differs: not a quotation, and then no evidence at all
        self.assertEqual(len(grade("")), 1)                              # an empty slot is missing evidence, not a failed citation
        self.assertEqual(len(grade("", evidence=False)), 1)               # a line that moves the score needs its quotation
        self.assertEqual(grade("", item="R2", evidence=False), [])        # "nothing was earned" has nothing to quote
        # A deduction rubric keeps the rule for every line, including one worth nothing: there, "no deduction" is the claim of full credit.
        deduct = rubrics.parse("-1 Error", 10)
        raw = {"applications": [{"rubric_item_id": deduct["items"][0]["id"], "applied": True, "reason": "", "evidence": [], "origin_error_id": None, "confidence": .95, "ambiguity": None}],
               "interpretation": {"error_graph": [], "final_answers": []}, "confidence": .95, "rubric_ambiguities": [], "review_reasons": []}
        self.assertTrue(recognition.validate_grade(raw, reading, deduct, 10, 1)["review_reasons"])

    def test_a_neighbouring_line_is_kept_where_found_and_an_unticked_line_never_flags_for_its_quotation(self):
        # Requirement, 2026-09-21: of 82 refused quotations 17 named a neighbouring line and 7 belonged to a line nobody ticked.
        from grading.models import transcript
        element = lambda value, **more: {"type": "text", "text": value, "latex": "", "bbox": None, "crop": 0, "confidence": .9, **more}
        def reading(struck=False):
            return transcript({"plain_text": "", "latex": "", "confidence": .9, "ambiguities": [], "lines": [
                {"id": "line-1", "elements": [element("Function:")]},
                {"id": "line-2", "elements": [element("P(x)=42x-190,000", **({"state": "crossed_out"} if struck else {}))]},
                {"id": "line-3", "elements": [element("R(x)=76x")]}]})
        rules = rubrics.validate({"mode": "positive", "items": [{"id": "R1", "description": "Correct function", "points": 2}, {"id": "R2", "description": "Wrote the revenue", "points": 1}]}, 3)
        def grade(quote, line="line-1", other="R(x)=76x", struck=False):
            raw = {"applications": [{"rubric_item_id": "R1", "applied": True, "reason": "", "evidence": [{"line_id": line, "student_text": quote, "bbox": None, "crop": 0}], "origin_error_id": None, "confidence": .95, "ambiguity": None},
                                    {"rubric_item_id": "R2", "applied": False, "reason": "", "evidence": [{"line_id": "line-3", "student_text": other, "bbox": None, "crop": 0}], "origin_error_id": None, "confidence": .95, "ambiguity": None}],
                   "interpretation": {"error_graph": [], "final_answers": [{"part": "", "text": quote, "line_id": line}]}, "confidence": .95, "rubric_ambiguities": [], "review_reasons": []}
            return recognition.validate_grade(raw, reading(struck), rules, 3, 1)
        found = grade("P(x)=42x-190,000")
        self.assertEqual(found["review_reasons"], [])
        self.assertEqual([(e["line_id"], e["named_line_id"]) for e in found["applications"][0]["evidence"]], [("line-2", "line-1")])
        self.assertEqual([(a["line_id"], a["named_line_id"]) for a in found["interpretation"]["final_answers"]], [("line-2", "line-1")])
        self.assertNotIn("named_line_id", grade("P(x)=42x-190,000", "line-2")["applications"][0]["evidence"][0])
        # The line nobody ticked: its quotation is dropped and counted, and flags nothing. The ticked line keeps the rule.
        unticked = grade("P(x)=42x-190,000", other="the revenue function")
        self.assertEqual((unticked["review_reasons"], unticked["unticked_quotes_dropped"], unticked["applications"][1]["evidence"]), ([], 1, []))
        self.assertNotIn("unticked_quotes_dropped", found)
        self.assertEqual(len(grade("P(x)=42x-19,000")["review_reasons"]), 3)  # another value: no quotation, no evidence, no final answer
        # A crossed-out neighbour is never the final answer, however the line was named.
        self.assertIn("A reported final answer is on a line the student crossed out.", grade("P(x)=42x-190,000", struck=True)["review_reasons"])

    def test_metrics_separate_ocr_only_and_grading(self):
        expected = {"transcript":plain_transcript("x = 8"),"applications":[],"score":{"score":10}}
        result = report([{"expected":expected,"proposed":{"transcript":plain_transcript("x = 5")},"rubric":{"items":[]}}])
        self.assertGreater(result["text_cer"], 0)
        self.assertIsNone(result["exact_score_agreement"])
        self.assertIsNone(result["human_override_rate"])
