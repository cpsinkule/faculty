"""Regressions from the grading refactor sweep. Synthetic readings and fake subscription only."""
import io
import json
import threading
import time
import unittest
import uuid
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import Mock, patch
from werkzeug.exceptions import Conflict

from grading import arbitration, config, consensus, evaluate, flag_resolution, model_catalog, models, recognition, rubric_generation, rubric_quality, rubrics, scoring
from grading.models import plain_transcript
from grading.providers import agent_sdk, bundles, codex_sdk, tool_proxy, tools
from grading.providers.base import Abort, Cancelled, ProviderError, abortable
from grading.providers.cloud import HostedEndpoint, provider
from grading.regrade_apply import RegradeApply
from grading.answer_groups import AnswerGroupsMixin
from grading.acceptance import AcceptanceMixin, upper_bound
from grading.certification import CertificationService
from grading.jobs import JobsMixin
from grading.intake import IntakeMixin
from grading.location_checks import LocationChecksMixin
from grading.makeup_drafts import MakeupDraftsMixin
from grading.quiz_drafts import QuizDraftsMixin
from grading.review import ReviewMixin
from test_agent_sdk import FakeBinary, PROBE_SCHEMA, subscription


class ReadingComparisons(unittest.TestCase):
    def test_large_sample_confidence_bound_keeps_its_uncertainty_margin(self):
        from decimal import Decimal, localcontext
        errors, count = 600, 1200
        bound = upper_bound(errors, count)
        self.assertGreater(bound, .52)
        self.assertLess(bound, .53)
        # Independent high-precision summation verifies the defining 5% binomial tail (GRADING benchmark policy).
        with localcontext() as context:
            context.prec = 70
            rate = Decimal(str(bound))
            term = total = (1 - rate) ** count
            for k in range(errors):
                term *= Decimal(count - k) / (k + 1) * rate / (1 - rate)
                total += term
        self.assertAlmostEqual(float(total), .05, places=10)

    def test_disagreements_keep_their_order_spans_and_original_pixel_locations(self):
        candidates = []
        for name, reading, box in (("a", "x + 1 = 2", [0, 0, .8, .2]),
                                   ("b", "x - 1 = 3", [0, .2, .8, .4]),
                                   ("c", "x + 1 = 4", [0, .4, .8, .6])):
            value = plain_transcript(reading)
            value["lines"][0]["elements"][0]["bbox"] = box
            candidates.append({"provider": name, "transcript": value})
        result = consensus.align(candidates)
        self.assertEqual(result["agreement"], .7)
        self.assertEqual([(d["providers"], d["first_span"], d["second_span"], d["first"], d["second"])
                          for d in result["disagreements"]],
                         [(["a", "b"], [1, 2], [1, 2], ["+"], ["-"]),
                          (["a", "b"], [4, 5], [4, 5], ["2"], ["3"]),
                          (["a", "c"], [4, 5], [4, 5], ["2"], ["4"])])
        for disagreement in result["disagreements"]:
            self.assertEqual([location["provider"] for location in disagreement["locations"]], disagreement["providers"])
            self.assertEqual(disagreement["bbox"], [0, 0, .8, .2])
            self.assertTrue(disagreement["grading_critical"])

    def test_many_disagreements_tokenize_each_reading_once_per_comparison(self):
        readings = ["\n".join(f"x_{index} = {value}" for index in range(30)) for value in (1, 2, 3)]
        candidates = [{"provider": str(index), "transcript": plain_transcript(reading)} for index, reading in enumerate(readings)]
        with patch.object(consensus, "tokens", wraps=consensus.tokens) as tokenize:
            result = consensus.align(candidates)
        self.assertEqual(len(result["disagreements"]), 60)
        self.assertLessEqual(tokenize.call_count, 3 * (30 + 1))
        # PERFORMANCE: the memo lasts this read only; changed reading geometry must appear on the next comparison.
        candidates[0]["transcript"]["lines"][0]["elements"][0]["bbox"] = [0, 0, 1, 1]
        changed = consensus.align(candidates)
        self.assertEqual(changed["disagreements"][0]["locations"][0]["bbox"], [0, 0, 1, 1])


class ModelValidation(unittest.TestCase):
    def setUp(self):
        self.reading = plain_transcript("x = 8")
        self.rubric = rubrics.validate({"mode": "positive", "items": [{"id": "R1", "description": "Correct", "points": 1}]}, 1)
        self.grade = {"applications": [{"rubric_item_id": "R1", "applied": True, "confidence": 1,
                                        "evidence": [{"line_id": "line-1", "student_text": "x = 8"}]}],
                      "confidence": 1}

    def test_malformed_nested_grade_values_fail_validation_without_type_errors(self):
        for mutate in (lambda g: g["applications"][0].update(rubric_item_id=[]),
                       lambda g: g["applications"][0].update(evidence=[None]),
                       lambda g: g.update(interpretation={"error_graph": [False]})):
            value = deepcopy(self.grade)
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                recognition.validate_grade(value, self.reading, self.rubric, 1, 1)
        with self.assertRaises(ValueError):
            scoring.score(self.rubric, [{"rubric_item_id": {}, "applied": True}], 1)
        with self.assertRaises(ValueError):
            scoring.score(self.rubric, [], 1, adjustments=[None])
        for ruling in ({"decision": "adopt", "adopt": ["first"]},
                       {"decision": "adopt", "adopt": "first", "flag_rulings": [{"flag": {}, "resolved": True}]}):
            with self.subTest(ruling=ruling), self.assertRaises(ValueError):
                arbitration.validate(ruling, {"first": "step"}, [{"id": "F1"}], lambda value: value)

    def test_malformed_optional_line_references_keep_existing_drop_and_review_rules(self):
        value = deepcopy(self.reading)
        value["layout_relations"] = [{"from_line": [], "to_line": "line-1", "kind": "continuation", "uncertain": True}]
        result = models.transcript(value)
        self.assertEqual((result["layout_relations"], result["layout_notes_dropped"]), ([], 1))
        value["reading_alternatives"] = [{"line_id": {}, "options": ["8", "9"]}]
        with self.assertRaises(ValueError):
            models.transcript(value)
        grade = deepcopy(self.grade)
        grade["applications"][0]["evidence"][0]["line_id"] = []
        result = recognition.validate_grade(grade, self.reading, self.rubric, 1, 1)
        self.assertEqual(result["applications"][0]["evidence"], [])
        self.assertTrue(result["review_reasons"])

    def test_huge_integer_points_fail_as_a_validation_error(self):
        with self.assertRaises(ValueError):
            models.number(10 ** 400, "Points", 0, 100)
        self.assertEqual(rubric_generation.stated_parts({"parts": [{"part": "a", "points": 10 ** 400},
                                                                  {"part": "b", "points": float("inf")},
                                                                  {"part": "c", "points": 2}]}), {"c": 2})

    def test_rubric_draft_requests_validate_refs_before_work(self):
        service = object.__new__(rubric_generation.RubricGenerationMixin)
        for method, body in ((service.queue_answer_key_reading, {"question_ids": [{}]}),
                             (service.queue_rubric_versions, {"templates": [{"key": {}, "template_id": "template"}]})):
            with self.subTest(method=method.__name__), self.assertRaises(ValueError):
                method("plan", {"request_id": str(uuid.uuid4()), **body})
        with self.assertRaises(ValueError):
            service.accept_rubric_generation("plan", "generation", {"request_id": str(uuid.uuid4()), "questions": [{"draft": []}]})

    def test_optional_parallel_links_drop_malformed_refs(self):
        family = [{"exam_version": letter, "question_id": letter, "rubric": self.rubric} for letter in ("A", "B")]
        for bad in ({"version": {}, "item_id": "R1"}, {"version": "A", "item_id": []}):
            with self.subTest(bad=bad):
                self.assertEqual(rubric_quality.parallel_links({"parallel_lines": [
                    {"kind": "same", "lines": [bad, {"version": "B", "item_id": "R1"}]}]}, family), ([], 1))

    def test_finalization_cache_is_bounded_and_retains_current_record(self):
        service = object.__new__(rubric_quality.RubricQualityMixin)
        service.path = "synthetic-catalog"
        db = Mock()
        db.execute.return_value.fetchone.return_value = {"policy_json": '{"inputs": {}, "questions": []}'}
        with patch.object(rubric_quality, "_REVIEWS", {}), patch.object(rubric_quality, "_REVIEWS_LIMIT", 3):
            for index in range(10):
                record = service.confirmation_record(db, str(index))
            self.assertEqual(len(rubric_quality._REVIEWS), 3)
            self.assertIs(service.confirmation_record(db, "9"), record)
            self.assertEqual(db.execute.call_count, 10)
        with self.assertRaises(ValueError):
            service.clarify_question("plan", {"confirm_review": True, "questions": [{"question_id": []}]})

    def test_malformed_flag_ref_does_not_discard_other_responses(self):
        rows = [{"ref": "R1", "rubric": self.rubric, "labels": ["first"], "flags": [], "graded": lambda value: value}]
        answer = {"rulings": [{"ref": {}}, {"ref": "R1", "decision": "instructor", "reason": "No visible work"}],
                  "clarifications": [{"line_id": {}, "clarification": "Bad reference"}]}
        found, clarified, _ = flag_resolution.rulings_of(answer, rows)
        self.assertEqual(found["R1"]["decision"], "unreliable")
        self.assertEqual(clarified, [])

    def test_location_batch_and_scan_identifiers_fail_before_reading(self):
        service = object.__new__(LocationChecksMixin)
        for method in (service.retry_all_locations, service.confirm_all_locations):
            for bad in ({}, []):
                with self.subTest(method=method.__name__, bad=bad), self.assertRaises(ValueError):
                    method("plan", {"confirm_locations": True, "questions": [{"question_id": bad}]})
        intake = object.__new__(IntakeMixin)
        for bad in ({"term_id": []}, {"term_id": "term", "assignment_id": {}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                intake.associate_scan("message", {"request_id": "request", **bad})

    def test_regrade_apply_validates_publication_ids_before_reading_or_writing(self):
        # No services are present: malformed confirmations must fail before any catalog or outside-service access (REGRADES).
        panel = object.__new__(RegradeApply)
        data = {"request_id": str(uuid.uuid4()), "confirmation": "apply", "fingerprint": "a" * 64}
        for malformed in ({}, "123", [[]], [{}], ["123", 1]):
            with self.subTest(published=malformed), self.assertRaises(ValueError):
                panel.apply("plan", "message", {**data, "published": malformed})

    def test_malformed_compute_choices_are_validation_errors(self):
        for settings in ({"default_tier": []}, {"default_tier": {}}, {"yardstick": ["model"]},
                         {"tiers": {"cloud_standard": {"primary": ["model"]}}},
                         {"role_fallbacks": {"cloud_standard": {"reader": {"name": "model"}}}}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                config.validate(settings, saving=True)

    def test_invalid_or_duplicate_group_members_fail_before_any_review_write(self):
        service = object.__new__(AnswerGroupsMixin)  # no catalog, lock or save method: invalid input must reach none of them
        common = {"confirm_review": True, "inspected_each": True}
        for method in (service.approve_group, service.approve_proposal_group):
            for members in ([{"id": "one"}, {"id": "one"}], [{"id": []}], [None]):
                with self.subTest(method=method.__name__, members=members), self.assertRaises(ValueError):
                    method("plan", {**common, "responses": members})
            with self.subTest(method=method.__name__, rubric=True), self.assertRaises(ValueError):
                method("plan", {**common, "responses": [{"id": "one"}], "selected_items": [{}], "applied": [{}]})

    def test_malformed_completion_choices_preserve_the_failure_receipt(self):
        for choices in ([None], [False], [4], ["not an object"], {}, "not a list"):
            raw = {"choices": choices, "usage": {"prompt_tokens": 1}}
            with self.subTest(choices=choices), self.assertRaises(ProviderError) as caught:
                HostedEndpoint.parse(raw, {})
            self.assertIs(caught.exception.confirmed, True)
            self.assertIs(caught.exception.raw, raw)
            self.assertEqual(caught.exception.confirmation, {"malformed": True, "finish_reason": None})

    def test_bad_batch_questions_fail_before_catalog_reads(self):
        service = object.__new__(JobsMixin)
        for body in ([], {"question_ids": [{}]}, {"question_ids": [[]]}, {"question_ids": ["one", "one"]},
                     {"question_ids": ["one"], "mode": {}}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                service.preview("plan", body)

    def test_certification_preview_rejects_malformed_category_and_column_before_reading(self):
        service = object.__new__(CertificationService)
        for book in ({"category_id": {}}, {"category_id": []}, {"column": {"action": []}}, {"column": {"item_id": {}}}):
            with self.subTest(book=book), self.assertRaises(ValueError):
                service.preview("plan", {"gradebook": book})

    def test_makeup_approval_validates_revision_before_any_read(self):
        service = object.__new__(MakeupDraftsMixin)
        for revision in ([], {}, None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                service.approve_makeup_draft("draft", {"request_id": "request", "confirmation": "approve", "revision_id": revision})

    def test_quiz_creation_validates_owner_and_example_id_shapes_before_reading(self):
        service = object.__new__(QuizDraftsMixin)
        body = {"request_id": "request", "term_id": "term", "title": "Quiz", "total": 5, "versions": 1, "instructions": "Functions"}
        for malformed in ({"term_id": []}, {"examples": [{}]}, {"examples": [[]]}):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                service.create_quiz_draft({**body, **malformed}, [])

    def test_acceptance_checks_evidence_after_taking_the_lifecycle_lock(self):
        service = object.__new__(AcceptanceMixin)
        gate = {"eligible": True, "upper_bound_95": .009, "reviewed": 300, "overturned": 0, "more_reviews_needed": 0}

        @contextmanager
        def lock():
            # Another review committed just before our lock was acquired. The old pre-lock report is now stale.
            gate.update(eligible=False, upper_bound_95=.016, reviewed=301, overturned=1, more_reviews_needed=200)
            yield

        service.rosters = Mock(lock=lock)
        service.settings = lambda: {"settings": {"error_limit": .01, "audit_share": .1}}
        service.acceptance_report = lambda *args: {"reports": [{"blind": deepcopy(gate)}]}
        with self.assertRaisesRegex(Conflict, "1 overturned in 301 blind reviews"):
            service.enable_acceptance("plan", "question", {"scope_sha256": "a" * 64, "confirm_automatic_acceptance": True})

    def test_invalid_bulk_approvals_fail_before_any_read_or_write(self):
        service = object.__new__(ReviewMixin)
        for responses in ([None], [{"id": []}], [{"id": "one"}, {"id": "one"}]):
            with self.subTest(responses=responses), self.assertRaises(ValueError):
                service.approve_many("plan", {"responses": responses, "confirm_review": True})


class DraftingToolProtocol(unittest.TestCase):
    def test_quiz_print_attachment_conflicts_are_replays_only_if_the_job_was_attached(self):
        service = object.__new__(QuizDraftsMixin)
        before = {"version": 1, "batches": []}
        service.exams = Mock()
        service.exams.get.return_value = before
        service.exams.attach_batch.side_effect = Conflict("Exam changed")
        with self.assertRaises(Conflict):
            service._attach_quiz_batch("exam", "job")
        service.exams.get.side_effect = [before, {"version": 2, "batches": [{"id": "job"}]}]
        service._attach_quiz_batch("exam", "job", sitting_id="makeup")
        self.assertEqual(service.exams.attach_batch.call_args.args, ("exam", {"version": 1, "batch_id": "job", "sitting_id": "makeup"}))

    def test_public_host_cache_is_bounded_and_retains_recently_used_models(self):
        catalog = model_catalog.ModelCatalog()
        with patch.object(model_catalog, "HOSTS_MAX", 2), patch.object(model_catalog, "request_json", return_value={"data": {"endpoints": []}}) as fetch:
            first = catalog.hosts("openrouter", "synthetic/first")
            catalog.hosts("openrouter", "synthetic/second")
            self.assertIs(catalog.hosts("openrouter", "synthetic/first"), first)
            catalog.hosts("openrouter", "synthetic/third")
            self.assertEqual(len(catalog.hosts_cache), 2)
            self.assertNotIn(("openrouter", "synthetic/second"), catalog.hosts_cache)
            self.assertEqual(fetch.call_count, 3)
        for raw in (None, [], False):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                model_catalog.normalize(raw)

    def test_offline_comparisons_do_not_resend_an_uncertain_provider_request(self):
        record = {"response_id": "response", "final": {"score": {"score": 1}},
                  "proposal": {"transcript": models.plain_transcript("x = 1")}, "question": {}, "rubric": {}}
        endpoint = {"name": "fake", "model": "fake", "lineage": "fake", "modality": "text", "max_inflight": 1}
        for confirmed, attempts in ((False, 1), (True, 3)):
            provider = Mock()
            provider.submit.side_effect = ProviderError("Synthetic interruption", confirmed=confirmed, fixable=True)
            with self.subTest(confirmed=confirmed), patch.object(evaluate, "load", return_value={"records": [record]}), \
                    patch.object(evaluate.providers, "provider", return_value=provider):
                result = evaluate.run_endpoint("synthetic.json", endpoint)
            self.assertEqual(provider.submit.call_count, attempts)
            self.assertEqual(result["predictions"]["response"]["failed"], "Synthetic interruption")

    def test_malformed_model_messages_leave_the_tool_session_usable(self):
        session = tools.ToolSet("test", [tools.Tool("read", "Read", {}, lambda arguments: "ok")], budget=5, guide="Read.").session()
        for malformed in ([], False, None):
            self.assertTrue(session._answer(malformed)["error"])
        self.assertTrue(session._answer({"op": "call", "name": [], "arguments": {}})["error"])
        result = session._answer({"op": "call", "name": "read", "arguments": {}})
        self.assertEqual((result["text"], result["error"]), ("ok", False))

    def test_proxy_answers_invalid_json_shapes_and_continues_to_the_next_request(self):
        incoming = io.StringIO('[]\nnull\n{"jsonrpc":"2.0","id":3,"method":"ping"}\n')
        outgoing = io.StringIO()
        tool_proxy.serve(None, stdin=incoming, stdout=outgoing)
        result = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual([message["error"]["code"] for message in result[:2]], [-32600, -32600])
        self.assertEqual(result[2], {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_subscription_streams_skip_nonobject_json_before_the_valid_answer(self):
        cases = ((agent_sdk, agent_sdk.AgentSdkEndpoint, "claude-sonnet-5",
                  [{"type": "result", "subtype": "success", "result": "kept"}]),
                 (codex_sdk, codex_sdk.CodexEndpoint, "gpt-6.1-sol",
                  [{"type": "item.completed", "item": {"type": "agent_message", "text": "kept"}},
                   {"type": "turn.completed", "usage": {}}]))
        for module, endpoint, model, events in cases:
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = ('null\n[]\n"diagnostic"\n' + '\n'.join(json.dumps(event) for event in events), "")
            with self.subTest(model=model), patch.object(module, "cli", return_value="/synthetic/program"), \
                    patch.object(module, "signed_in", return_value=True), patch.object(module.subprocess, "Popen", return_value=process):
                result, _ = endpoint({"name": "fake", "model": model, "modality": "text"})._run("Read.", [], None)
            self.assertEqual(result["result"], "kept")


class BundleCancellation(FakeBinary):
    def test_a_cancelled_middle_member_never_reuses_a_surviving_members_label(self):
        connection = provider(subscription(name="sweep-cancellation", bundle_size=4, sessions=1))
        semaphore = connection._semaphore()
        semaphore.acquire()
        held, threads, outcomes = True, [], {}
        stops = [Abort() for _ in range(4)]

        def ask(index):
            request = {"prompt": "probe", "payload": {"task": "Double it.", "double": index}, "images": [],
                       "schema": PROBE_SCHEMA, "bundle": {"key": ["sweep", "q", "s"]}}
            try:
                with abortable(stops[index]):
                    outcomes[index] = connection.submit(request)
            except (ProviderError, Cancelled) as error:
                outcomes[index] = error

        def joined(count):
            until = time.monotonic() + 5
            while time.monotonic() < until:
                with bundles.BundleMixin._guard:
                    if any(len(bundle.items) == count for bundle in bundles.BundleMixin._open.values()):
                        return
                time.sleep(.01)
            self.fail(f"The bundle never reached {count} members.")

        try:
            for index in range(3):
                thread = threading.Thread(target=ask, args=(index,))
                threads.append(thread)
                thread.start()
                joined(index + 1)
            stops[1].stop("Cancelled the middle response before the bundle was sent.")
            threads[1].join(3)
            self.assertIsInstance(outcomes.get(1), Cancelled)
            joined(2)
            thread = threading.Thread(target=ask, args=(3,))
            threads.append(thread)
            thread.start()
            joined(3)
            semaphore.release()
            held = False
            for thread in threads:
                thread.join(10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual([outcomes[index]["result"]["output"]["sum"] for index in (0, 2, 3)], [0, 4, 6])
            self.assertEqual({outcomes[index]["result"]["metadata"]["bundle"]["item"] for index in (0, 2, 3)},
                             {"R1", "R3", "R4"})
            self.assertEqual([call["items"] for call in self.invocations()], [3])
        finally:
            if held:
                semaphore.release()
            for stop in stops:
                stop.stop("Test cleanup.")
            for thread in threads:
                thread.join(10)


if __name__ == "__main__":
    unittest.main()
