"""Synthetic FRQ contracts and real catalog/worker/review integration."""
import copy
import io
import json
import unittest
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from grading import consensus, rubrics
from grading.models import plain_transcript
from grading.scoring import score
from grading.providers.base import ProviderError
from rosters import connect
import test_exam_grading as exam_fixture
from frq_fixtures import finalize_rubrics


class RubricContracts(unittest.TestCase):
    def test_negative_positive_and_decimal_bounds(self):
        negative = rubrics.parse("-0.1 Arithmetic error\n-0.2 Sign error", 1)
        applications = [{"rubric_item_id": i["id"], "applied": True} for i in negative["items"]]
        self.assertEqual(score(negative, applications, 1)["score"], .7)
        positive = rubrics.parse("2 Correct setup\n3 Correct result", 5, mode="positive")
        self.assertEqual(score(positive, [{"rubric_item_id": "R1", "applied": True}], 5)["score"], 2)
        negative["floor"] = .9
        self.assertEqual(score(negative, applications, 1)["score"], .9)

    def test_groups_dependencies_follow_through(self):
        rules = rubrics.validate({"items": [{"id": "R1", "description": "Error", "points": -1, "group": "G"},
            {"id": "R2", "description": "Error 2", "points": -2, "group": "G"}],
            "groups": [{"id": "G", "selection": "single"}]}, 5)
        apps = [{"rubric_item_id": "R1", "applied": True}, {"rubric_item_id": "R2", "applied": True}]
        with self.assertRaises(ValueError):
            score(rules, apps, 5)
        rules["groups"][0]["selection"] = "multiple"
        rules["items"][0]["follow_through"] = True
        for app in apps:
            app["origin_error_id"] = "E1"
        with self.assertRaises(ValueError):
            score(rules, apps, 5)

    def test_structural_consensus_retains_critical_differences(self):
        for left, right in (("x^2", "x^{2}"), (r"\frac12", r"\frac{1}{2}"), ("3x", r"3 \cdot x")):
            self.assertEqual(consensus.tokens(left), consensus.tokens(right))
        for left, right in (("x+2", "x-2"), ("x<2", "x<=2"), ("x^2", "x2"), (r"\frac{1}{7}", r"\frac{7}{1}")):
            result = consensus.align([{"provider": "a", "transcript": plain_transcript(left)}, {"provider": "b", "transcript": plain_transcript(right)}])
            self.assertTrue(result["disagreements"])

    def test_rubric_interpretation(self):
        rules = rubrics.parse("-1 arithmetic error. Do not penalize subsequent work that is correct based on that error.", 8)
        self.assertTrue(rules["items"][0]["follow_through"])
        self.assertFalse(rules["items"][0]["repeat_penalty_for_same_originating_error"])


class FakeProvider:
    fail = False

    def health(self):
        return {"status": "available"}

    def estimate(self, count):
        return {"responses": count, "estimated_cost": count * .01, "model": "fixture"}

    def recognize(self, image):
        if self.fail:
            raise ProviderError("Synthetic endpoint unavailable.")
        return {"provider": "glm", "transcript": plain_transcript("3x - 8 = 10", confidence=.97), "raw": {"text": "3x - 8 = 10"}, "metadata": {"model": "fixture-ocr"}}

    def verify(self, payload, images):
        return {"output": {"transcript": plain_transcript("3x - 8 = 10", confidence=.97), "visual_confidence": .97}, "raw": {}, "metadata": {"model": "fixture-verify"}}

    def compile(self, source, maximum, *, rubric=None):
        return {"output": copy.deepcopy(rubric) if rubric is not None else rubrics.parse(source, maximum), "raw": {"fixture": True}, "metadata": {"model": "fixture-compiler", "prompt": "compile-v1.txt"}}

    def grade(self, payload, images):
        if self.fail:
            raise ProviderError("Synthetic grader unavailable.")
        return {"output": {"applications": [{"rubric_item_id": "R1", "applied": True, "reason": "Written arithmetic error", "origin_error_id": "E1",
            "confidence": .98, "evidence": [{"line_id": "line-1", "student_text": "3x - 8 = 10", "bbox": [.1,.1,.8,.3], "crop": 0}]}],
            "interpretation": {"meaning": "Student wrote 8", "error_graph": [{"id": "E1", "caused_by": [], "line_ids": ["line-1"], "description": "Independent arithmetic error"}]},
            "confidence": .98, "rubric_ambiguities": [], "review_reasons": []}, "raw": {"fixture": True}, "metadata": {"model": "fixture-grade"}}


class FrqWorkflowTests(unittest.TestCase):
    managed = False
    document_version = exam_fixture.ExamGradingTests.document_version
    ready_batch = exam_fixture.ExamGradingTests.ready_batch
    reviewed_source = exam_fixture.ExamGradingTests.reviewed_source

    def setUp(self):
        exam_fixture.ExamGradingTests.setUp(self)
        self.frq = self.app.extensions["frq_grading"]
        self.plan = self.frq.create_assignment({"event_id": self.event["id"]})
        self.plan = self.frq.save_question(self.plan["id"], {"version": self.plan["version"], "question_key": "q1", "assignment_version_id": self.version_id,
            "content": {"title": "Question 1", "max_points": 10, "problem": "Solve", "regions": [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .4}}]}})
        self.qid = self.plan["questions"][0]["question_id"]
        self.configure_rubric()
        self.fake = FakeProvider()
        self.addCleanup(patch.stopall)
        patch.object(self.frq, "provider", return_value=self.fake).start()
        from frq_fixtures import page
        image = page()
        patch("grading.extraction.extract", return_value={"metadata": {"registered": True, "crop_bounds": [0,0,100,100]},
            "images": {k: image for k in ("original", "grayscale", "contrast", "ink", "cloud")}}).start()

    def configure_rubric(self, points=1):
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        self.plan = self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "-1 Arithmetic error",
            "rules": {"items": [{"id": "R1", "description": "Arithmetic error", "points": -points, "original_source_text": "-1 Arithmetic error"}]}})
        q = self.plan["questions"][0]
        self.plan = self.frq.approve_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"]})
        finalize_rubrics(self.frq, self.plan["id"])
        self.plan = self.frq.assignment(self.plan["id"])

    def queue(self, operation="recognize", **extra):
        import uuid
        finalize_rubrics(self.frq, self.plan["id"])  # Every batch needs the confirmed finalization of its current rubrics.
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": operation, **extra}
        command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
        return self.frq.create_job(self.plan["id"], command), command

    def finish(self, job):
        for _ in range(20):
            if not self.frq.process_one():
                break
        result = self.frq.job(job["id"])
        self.assertEqual(result["status"], "review_ready", result)
        return self.frq.response(result["tasks"][0]["response_id"])

    def approve(self, response, **extra):
        return self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"],
            "status": "approved", "confirm_review": True, "selected_items": ["R1"], **extra})

    def test_malformed_question_and_rubric_fields_fail_without_saving(self):
        # GRADING: invalid nested input cannot create a question revision or alter its pinned rubric.
        question = self.plan["questions"][0]
        command = {"version": self.plan["version"], "question_key": question["question_key"],
                   "assignment_version_id": self.version_id, "content": question["content"]}
        malformed = [{**command, "assignment_version_id": []}]
        for fields in ({"template_id": {"id": "template"}}, {"regions": [None]}, {"examples": [False]}):
            malformed.append({**command, "content": {**question["content"], **fields}})
        for body in malformed:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.frq.save_question(self.plan["id"], body)
        for item_id in ([], {}):
            with self.subTest(item_id=item_id), self.assertRaises(ValueError):
                self.frq.edit_rubric(self.plan["id"], self.qid, {"version": question["version"],
                    "rubric_id": question["rubric_id"], "sha256": question["rubric"]["sha256"],
                    "lines": [{"item_id": item_id, "description": "Changed"}]})
            with self.subTest(link_item_id=item_id), self.assertRaises(ValueError):
                self.frq.set_rubric_links(self.plan["id"], {"operations": [{"op": "unlink",
                    "line": {"question_id": self.qid, "item_id": item_id}}]})
        after = self.frq.assignment(self.plan["id"])
        self.assertEqual(after["version"], self.plan["version"])
        self.assertEqual(after["questions"][0]["rubric_id"], question["rubric_id"])
        self.assertEqual(after["questions"][0]["question_revision_id"], question["question_revision_id"])

    def test_end_to_end_idempotency_and_export(self):
        job, command = self.queue()
        self.assertEqual(self.frq.create_job(self.plan["id"], command)["id"], job["id"])
        response = self.finish(job)
        self.assertEqual(response["result"]["score"]["score"], 9)
        self.assertEqual(response["result"]["state"], "proposed")
        approved = self.approve(response)
        self.assertEqual(approved["review"]["score"]["score"], 9)
        content, _, _ = self.frq.export(self.plan["id"])
        self.assertEqual(json.loads(content)["records"][0]["final"]["status"], "approved")
        with connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_recalculating_points_drops_a_rubric_flag_that_the_current_wording_rule_no_longer_makes(self):
        """Results graded while a rubric was marked conditional keep that sentence in their retained grading step."""
        from grading.recognition import RUBRIC_EXCEPTIONS
        with patch("grading.recognition.current_condition_reasons", side_effect=lambda reasons, *_: reasons + [RUBRIC_EXCEPTIONS]), patch("grading.jobs.current_condition_reasons", side_effect=lambda reasons, *_: reasons + [RUBRIC_EXCEPTIONS]):
            marked = self.finish(self.queue()[0])  # As under the older, broader rule.
        self.assertIn(RUBRIC_EXCEPTIONS, marked["result"]["reasons"])
        self.assertEqual(marked["result"]["state"], "grading_review")
        with patch.object(self.frq, "provider", side_effect=AssertionError("No inference when recalculating")):
            refreshed = self.finish(self.queue("rescore")[0])
        self.assertNotIn(RUBRIC_EXCEPTIONS, refreshed["result"]["reasons"])
        self.assertEqual(refreshed["result"]["state"], "proposed")
        self.assertEqual(refreshed["result"]["score"], marked["result"]["score"])

    def test_a_response_with_nothing_to_reuse_is_named_in_the_preview_instead_of_refusing_the_whole_batch(self):
        command = {"request_id": "5b0e1d0c-3c55-4d0a-9d0f-1f3d8f3c0a11", "question_ids": [self.qid], "mode": "local"}
        for operation in ("grade", "rescore"):  # Nothing was read yet.
            found = self.frq.preview(self.plan["id"], {**command, "operation": operation})
            self.assertEqual((len(found["eligible"]), len(found["skipped"])), (0, 1))
            self.assertIn("Nothing is retained to reuse", found["skipped"][0]["reason"])
        self.assertEqual(len(self.frq.preview(self.plan["id"], {**command, "operation": "recognize"})["eligible"]), 1)
        self.finish(self.queue()[0])
        self.assertEqual(len(self.frq.preview(self.plan["id"], {**command, "operation": "rescore"})["eligible"]), 1)
        with self.assertRaises(ValueError):
            self.frq.preview(self.plan["id"], {**command, "operation": "publish"})

    def test_rubric_recalculation_and_human_correction_survive(self):
        response = self.finish(self.queue()[0])
        approved = self.approve(response, transcript=plain_transcript("3x - 8 = 10\nx = 8"))
        self.configure_rubric(.5)
        updated = self.finish(self.queue("rescore")[0])
        self.assertEqual(updated["result"]["score"]["score"], 9.5)
        # A decision made under the earlier rubric no longer counts (2026-09-25): it stays in the history, and the recalculated
        # proposal waits for the instructor.
        self.assertEqual((updated["review"], updated["earlier_review"]["id"]), (None, approved["review"]["id"]))
        self.assertIn("x = 8", updated["result"]["transcript"]["plain_text"])
        with self.assertRaises(Conflict):
            self.frq.export(self.plan["id"])

    def test_failed_provider_explicit_retry_and_partial_receipts(self):
        job, _ = self.queue()
        self.fake.fail = True
        for _ in range(8):
            self.frq.process_one()
        failed = self.frq.job(job["id"])
        self.assertEqual(failed["status"], "failed")
        rid = failed["tasks"][0]["response_id"]
        self.assertIsNone(self.frq.response(rid)["result"])
        with connect(self.path) as db:  # A failed task is named by its question in the batch, never by its student.
            self.assertEqual(failed["tasks"][0]["question_id"], db.execute("SELECT question_id FROM frq_responses WHERE id=?", (rid,)).fetchone()[0])
        self.assertFalse({"section_id", "enrollment_id"} & set(failed["tasks"][0]))
        self.fake.fail = False
        self.frq.retry_job(job["id"])
        self.finish(job)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_steps WHERE stage='crop:0'").fetchone()[0], 1)

    def test_stale_rubric_and_source_do_not_grade(self):
        job, _ = self.queue()
        self.configure_rubric(.5)
        self.frq.process_one()
        self.assertEqual(self.frq.job(job["id"])["status"], "failed")

    def test_a_cancelled_batch_does_no_more_work_and_a_proposal_is_shown_once_it_exists(self):
        job, _ = self.queue()
        self.frq.cancel_job(job["id"])
        self.assertFalse(self.frq.process_one())
        job, _ = self.queue()
        response = self.finish(job)
        self.assertFalse(response["hidden"])
        self.assertIn("applications", response["result"])

    def test_rescore_preserves_human_selections_and_adjustments_without_providers(self):
        response = self.finish(self.queue()[0])
        self.approve(response, selected_items=[], adjustments=[{"points":-.25,"reason":"Instructor adjustment"}])
        self.configure_rubric(.5)
        settings = self.frq.settings()
        self.frq.save_settings({"version":settings["version"],"settings":{**settings["settings"],"glm_enabled":False}})
        with patch.object(self.frq, "provider", side_effect=AssertionError("No inference during rescore")), \
                patch.object(self.frq, "cloud_provider", side_effect=AssertionError("No inference during rescore")):
            updated = self.finish(self.queue("rescore")[0])
        self.assertEqual(updated["result"]["score"]["score"], 9.75)
        self.assertEqual((updated["review"], updated["earlier_review"]["score"]["score"]), (None, 9.75))  # Kept in its history.

    def test_interrupted_worker_requires_explicit_retry(self):
        job, _ = self.queue()
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='running' WHERE job_id=?", (job["id"],))
        self.frq.process_one()
        failed = self.frq.job(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("interrupted", failed["tasks"][0]["error"])
        # Other bounded work (including scan-association catch-up) may proceed;
        # the interrupted grading task itself must remain failed until retried.
        for _ in range(5):
            if not self.frq.process_one():
                break
            self.assertEqual(self.frq.job(job['id'])['status'], 'failed')
        else:
            self.fail('Synthetic background work did not settle')
        self.frq.retry_job(job["id"])
        self.finish(job)

    def test_rescore_rejects_changed_solution_and_stale_human_selections(self):
        response = self.finish(self.queue()[0])
        self.approve(response)
        plan = self.frq.assignment(self.plan["id"])
        question = plan["questions"][0]
        self.frq.save_question(plan["id"], {"version":plan["version"], "question_key":question["question_key"],
            "assignment_version_id":question["assignment_version_id"], "content":{**question["content"], "expected_answer":"A corrected solution"}})
        self.configure_rubric(.5)
        with self.assertRaisesRegex(Conflict, "Question or solution changed"):
            self.queue("rescore")
        updated = self.finish(self.queue("grade")[0])
        with self.assertRaisesRegex(Conflict, "earlier question or solution"):
            self.queue("rescore")
        self.approve(updated)
        self.assertEqual(self.finish(self.queue("rescore")[0])["result"]["score"]["score"], 9.5)

    def test_malformed_model_output_cannot_be_approved_and_is_retained(self):
        job, _ = self.queue()
        with patch.object(self.fake, "grade", return_value={"output":{"applications":[{"rubric_item_id":"unknown"}]},"raw":{"private":"written work"},"metadata":{"model":"bad-fixture"}}):
            for _ in range(20):
                self.frq.process_one()
        task = self.frq.job(job["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertNotIn("written work", task["error"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rejected_outputs").fetchone()[0], 1)
        value = self.frq.response(task["response_id"])
        with self.assertRaises(Conflict):
            self.approve(value)

    def test_rubric_change_during_inference_retains_receipt_without_result(self):
        job, _ = self.queue()
        original = self.fake.grade
        def changed(*args):
            self.configure_rubric(.5)
            return original(*args)
        with patch.object(self.fake, "grade", side_effect=changed):
            for _ in range(20):
                self.frq.process_one()
        self.assertEqual(self.frq.job(job["id"])["status"], "failed")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_steps WHERE stage='grading'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_results").fetchone()[0], 0)

    def test_compiler_queue_is_idempotent_and_does_not_approve(self):
        command = {"request_id":"compile-fixture","source_text":"-1 Arithmetic error"}
        prepared = self.frq.compile_rubric(self.plan["id"], self.qid, command)
        self.assertEqual(self.frq.compile_rubric(self.plan["id"], self.qid, command)["id"], prepared["id"])
        self.frq.process_one()
        result = self.frq.preparation(prepared["id"])
        self.assertEqual(result["status"], "complete")
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        imported = self.frq.import_rubric(self.plan["id"], self.qid, {"version":q["version"],"source_text":command["source_text"],"preparation_id":prepared["id"]})
        self.assertIsNone(imported["questions"][0]["rubric"]["approved_at"])
        self.assertEqual(imported["questions"][0]["rubric"]["compiler"]["prompt"], "compile-v1.txt")

    def test_source_reader_keeps_multi_page_receipts(self):
        prepared = self.frq.upload_source(self.plan["id"], "rubric.pdf", b"%PDF synthetic", "source-fixture")
        with patch("document_identity.process.run", return_value={"total":2}), patch("faculty_ocr.recognize_artifact", side_effect=[{"text":"-1 First error"},{"text":"-2 Second error"}]):
            for _ in range(10):
                self.frq.process_one()
        result = self.frq.preparation(prepared["id"])
        self.assertEqual(result["status"], "complete", result)
        self.assertIn("Second error", result["result"]["source_text"])
        self.assertEqual(result["completed_parts"], 3)

    def test_gradescope_upload_compilation_and_review_keep_originals(self):
        from test_gradescope_rubrics import encode, export_rows
        rows = export_rows()
        rows.insert(1, ["submission", "question", "Synthetic private row", "Student", "001234567"])
        source = encode(rows)
        response = self.client.post(f"/api/grading/assignments/{self.plan['id']}/sources",
            data={"file": (io.BytesIO(source.encode()), "rubric.CSV"), "request_id": "gradescope-source"}, headers=exam_fixture.HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        uploaded = response.json
        self.assertEqual(uploaded["rubric_source"]["mode"], "positive")
        self.assertEqual(uploaded["rubric_source"]["max_points"], 10)
        self.assertEqual(uploaded["source_text"], source)
        _, retained = self.frq.inbox.read_artifact(uploaded["source_artifact_id"])
        self.assertEqual(retained, source.encode())
        with connect(self.path) as db:
            owner = db.execute("SELECT m.definition_id,m.term_id,m.section_id FROM inbox_messages m JOIN inbox_artifacts a ON a.message_id=m.id WHERE a.id=?", (uploaded["source_artifact_id"],)).fetchone()
        self.assertEqual(tuple(owner), (self.assignment["definition_id"], None, None))
        command = {"request_id": "gradescope-compile", "source_text": source, "format": "csv"}
        with patch.object(self.fake, "compile", wraps=self.fake.compile) as compiler:
            prepared = self.frq.compile_rubric(self.plan["id"], self.qid, command)
            self.frq.process_preparation_one()
        self.assertNotIn("Synthetic private row", str(compiler.call_args))
        result = self.frq.preparation(prepared["id"])
        self.assertEqual(result["status"], "complete", result["error"])
        question = self.frq.assignment(self.plan["id"])["questions"][0]
        imported = self.frq.import_rubric(self.plan["id"], self.qid, {"version": question["version"],
            "source_text": source, "preparation_id": prepared["id"], "source_artifact_id": uploaded["source_artifact_id"]})
        q = imported["questions"][0]
        self.assertEqual(q["rubric"]["source_text"], source)
        self.assertEqual(q["rubric"]["rules"]["items"][0]["description"], "Full credit")
        self.assertIsNone(q["rubric"]["approved_at"])
        approval = {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"]}
        with self.assertRaisesRegex(ValueError, "flagged rubric interpretations"):
            self.frq.approve_rubric(self.plan["id"], self.qid, approval)
        with self.assertRaises(Conflict):
            self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": source, "preparation_id": prepared["id"]})
        final = self.frq.approve_rubric(self.plan["id"], self.qid, {**approval, "acknowledge_rules": True})
        self.assertTrue(final["questions"][0]["rubric"]["approved_at"])

    def test_gradescope_mismatch_and_changed_model_points_never_import(self):
        from test_gradescope_rubrics import encode, export_rows
        source = encode(export_rows(4, [4, 1, 0]))
        with self.assertRaisesRegex(ValueError, "worth 4 points"):
            self.frq.compile_rubric(self.plan["id"], self.qid, {"request_id": "wrong-total", "source_text": source, "format": "csv"})
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        with self.assertRaisesRegex(ValueError, "worth 4 points"):
            self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": source, "format": "csv"})
        source = encode(export_rows())
        prepared = self.frq.compile_rubric(self.plan["id"], self.qid, {"request_id": "wrong-model-points", "source_text": source, "format": "csv"})
        output = rubrics.parse(source, 10, "csv")
        output["items"][1]["points"] = .5
        with patch.object(self.fake, "compile", return_value={"output": output, "raw": {}, "metadata": {}}):
            self.frq.process_preparation_one()
        failed = self.frq.preparation(prepared["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("point values", failed["error"])
        self.assertEqual(self.frq.assignment(self.plan["id"])["questions"][0]["rubric_id"], q["rubric_id"])

    def test_rubric_http_bounds_and_same_origin_checks(self):
        source_url = f"/api/grading/assignments/{self.plan['id']}/sources"
        self.assertEqual(self.client.post(source_url, json={}).status_code, 403)
        self.assertEqual(self.client.post(source_url, json={}, headers=exam_fixture.HEADERS).status_code, 415)
        self.assertEqual(self.client.post(source_url, json={}, headers={**exam_fixture.HEADERS, "Origin": "https://elsewhere.invalid"}).status_code, 403)
        from test_gradescope_rubrics import encode, export_rows
        source = encode(export_rows())
        source += encode([["submission", "question", "Synthetic", "Student", "", "", "", "", "", "", "", "", "", "", "Long comment " * 3000]])
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        url = f"/api/grading/assignments/{self.plan['id']}/questions/{self.qid}/rubrics"
        result = self.client.post(url, json={"version": q["version"], "source_text": source, "format": "csv"}, headers=exam_fixture.HEADERS)
        self.assertGreater(len(source), 32 * 1024)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(len(result.json["questions"][0]["rubric"]["rules"]["items"]), 4)
        oversized = self.client.post(url, data=b" " * (2 * 1024 * 1024 + 1), content_type="application/json", headers=exam_fixture.HEADERS)
        self.assertEqual(oversized.status_code, 413)

    def test_bulk_approval_dataset_benchmark_and_total_export(self):
        import tempfile
        from pathlib import Path
        from manage_grading import benchmark
        response = self.finish(self.queue()[0])
        self.frq.approve_many(self.plan["id"], {"responses":[{"id":response["id"],"version":response["version"]}],"confirm_review":True})
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "dataset.zip"
            with self.app.test_client().post(f"/api/grading/assignments/{self.plan['id']}/export", json={"kind":"dataset"}, headers=exam_fixture.HEADERS) as download:
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.mimetype, "application/zip")
                path.write_bytes(download.get_data())
            report = benchmark(path)
        self.assertTrue(any("glm only" in k for k in report["profiles"]))
        self.assertTrue(any("verifier" in k for k in report["profiles"]))
        self.assertTrue(any("grader" in k for k in report["profiles"]))
        content, _, _ = self.frq.export(self.plan["id"], "totals")
        self.assertIn(b"Complete", content)

    def test_migrations_apply_once_and_revisions_are_immutable(self):
        import sqlite3
        from grading.service import GradingService
        GradingService(self.frq.exams, self.frq.manual, self.frq.templates, self.frq.inbox)
        with connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE frq_rubric_revisions SET source_text='changed'")


class ManagedFrqTests(unittest.TestCase):
    managed = True
    setUp = FrqWorkflowTests.setUp
    document_version = FrqWorkflowTests.document_version
    ready_batch = FrqWorkflowTests.ready_batch
    reviewed_source = FrqWorkflowTests.reviewed_source
    configure_rubric = FrqWorkflowTests.configure_rubric
    queue = FrqWorkflowTests.queue
    finish = FrqWorkflowTests.finish
    approve = FrqWorkflowTests.approve
    test_managed_rubric_upload_owner_and_review = FrqWorkflowTests.test_gradescope_upload_compilation_and_review_keep_originals

    def test_managed_crops_and_review_in_verified_backup(self):
        import sqlite3
        response = self.finish(self.queue()[0])
        approved = self.approve(response)
        artifact_id = response["crops"][0]["images"]["original"]["artifact_id"]
        with self.rosters.lock():
            snapshot = self.storage.backup_catalog()
        name = snapshot["catalog_snapshot"] if isinstance(snapshot, dict) else snapshot
        with sqlite3.connect(self.storage.hdd / "workspace-backups" / name / "faculty.sqlite3") as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_reviews WHERE id=?", (approved["review"]["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox_artifacts WHERE id=?", (artifact_id,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
