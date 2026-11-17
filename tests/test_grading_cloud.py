"""Hosted endpoints, the retry policy, the parallel cloud lane and the dedicated provider, against a local fake host.

No credentials and no network: `fake_openai.FakeHost` is an OpenAI-compatible server on loopback. The real
catalog, worker lanes and review path are used; only GLM-OCR (the local GPU) is a fixture.
"""
import json
import os
import threading
import unittest
import uuid
from contextlib import nullcontext
from unittest.mock import patch


from grading import config
from grading.cloud import Limiter
from grading.providers import cloud as providers
from grading.providers.base import ProviderError
from rosters import connect
import test_frq_grading as grading_fixture
from fake_openai import FakeHost, default_answer, payload_of, prompt_name
from frq_fixtures import finalize_rubrics


def endpoint(host, name, **extra):
    return {**config.ENDPOINT, "name": name, "base_url": host.url, "model": f"fixture/{name}", "lineage": name.split("-")[0], "max_inflight": 4, **extra}


class CloudCase(unittest.TestCase):
    # Existing transport/legacy tests retain the workflow their assertions cover.
    # The replacement's integration suite explicitly opts into the current rule.
    current_standard = False
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric
    approve = grading_fixture.FrqWorkflowTests.approve

    def setUp(self):
        grading_fixture.FrqWorkflowTests.setUp(self)  # GLM-OCR and crop preparation are fixtures; every cloud call below is real HTTP.
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        self.configure()

    def configure(self, endpoints=None, **settings):
        endpoints = endpoints or [endpoint(self.host, "alpha-vision"), endpoint(self.host, "beta-text", modality="text"), endpoint(self.host, "gamma-vision")]
        tiers = {"cloud_standard": {"reader": endpoints[0]["name"], "primary": endpoints[0]["name"], "second": "", "verifier": ""}}
        current = self.frq.settings()
        clean = {**current["settings"], "endpoints": endpoints, "tiers": tiers, "yardstick": "", "max_retries": 3, **settings}
        return self.frq.save_settings({"version": current["version"], "settings": clean})

    def queue(self, mode="cloud_standard", questions=None, **extra):
        finalize_rubrics(self.frq, self.plan["id"])  # Every batch needs the confirmed finalization of its current rubrics.
        command = {"request_id": str(uuid.uuid4()), "question_ids": questions or [self.qid], "mode": mode, "operation": "recognize", **extra}
        with (nullcontext() if self.current_standard else patch("grading.standard.freeze")):
            command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
            return self.frq.create_job(self.plan["id"], command)

    def elapse(self):
        """Time passes: whatever the policy asked to wait for (Retry-After, a poll interval) is now due."""
        with connect(self.path) as db:
            return db.execute("UPDATE frq_tasks SET not_before='' WHERE status='pending' AND not_before!=''").rowcount

    def run_all(self, turns=80, service=None):
        for _ in range(turns):
            if not (service or self.frq).process_one() and not self.elapse():
                break

    def run_until_submitted(self, step="reconciling"):
        """Stop as soon as the provider holds a job for this component: recorded with its durable ID, not yet collected."""
        for _ in range(40):
            self.frq.process_one()
            found = self.calls(f"step='{step}'")
            if found:
                return found[0]
        self.fail("No cloud call was submitted.")

    def calls(self, where="1=1"):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM frq_cloud_calls WHERE {where} ORDER BY rowid")]


class HostedEndpointTests(CloudCase):
    def test_a_cloud_batch_starts_without_any_crop_approval_pause_and_records_what_left(self):
        job = self.queue()
        self.assertEqual((job["mode"], job["tier"], job["compute"]), ("nebius", "cloud_standard", "cloud_standard"))
        self.assertNotIn("awaiting_privacy", job)
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual(done["status"], "review_ready", done)
        response = self.frq.response(done["tasks"][0]["response_id"])
        self.assertEqual(response["result"]["score"]["score"], 10)  # The fake grader applies no deduction; points come from the backend.
        calls = self.calls()
        self.assertEqual([(c["step"], c["role"], c["state"], c["endpoint"], c["tier"]) for c in calls],
                         [("reconciling", "reader", "completed", "alpha-vision", "cloud_standard"), ("grade:primary", "primary", "completed", "alpha-vision", "cloud_standard")])
        for call in calls:
            usage = json.loads(call["usage_json"])
            self.assertGreater(usage["total_tokens"], 0)
            self.assertEqual((call["actual_model"], call["upstream"], call["lineage"], call["provider"]), ("fixture/alpha-vision", "FakeUpstream", "alpha", "hosted"))
            # This fixture's connections have no configured prices: their cost is unknown, never a free zero.
            self.assertEqual((call["cost"], call["cost_basis"]), (None, "unavailable: no configured prices"))
        sent = [r["body"] for r in self.host.requests]
        self.assertEqual([prompt_name(b) for b in sent], ["reconcile", "grade"])
        self.assertTrue(all(b["response_format"]["json_schema"]["strict"] and b["temperature"] == 0 and b["store"] is False for b in sent))
        self.assertTrue(all(any(part["type"] == "image_url" for part in b["messages"][1]["content"]) for b in sent))  # The image stays the source of truth.
        self.assertNotIn("rubric", json.dumps(payload_of(sent[0])))  # The reader never sees the rubric, the solution or the expected answer.
        self.assertNotIn("solution", json.dumps(payload_of(sent[0])))
        # Faculty owns the identity mapping (requirement, 2026-09-19): nothing that names the student leaves.
        for body in sent:
            outgoing = json.dumps(body)
            for private in (self.enrollment, "enrollment_id", "section_id"):
                self.assertNotIn(private, outgoing)
        steps = self.frq.job(job["id"], results=True)
        self.assertEqual(steps["progress"]["calls"], {"completed": 2})

    def test_token_usage_and_cost_are_recorded_per_request_and_summed_on_the_batch(self):
        self.configure([endpoint(self.host, "alpha-vision", price_in=1.0, price_out=2.0)])
        job = self.queue()
        self.run_all()
        calls = self.calls()
        expected = sum((json.loads(c["usage_json"])["prompt_tokens"] * 1.0 + json.loads(c["usage_json"])["completion_tokens"] * 2.0) / 1e6 for c in calls)
        self.assertAlmostEqual(sum(c["cost"] for c in calls), expected)
        self.assertAlmostEqual(self.frq.job(job["id"])["cost"], expected)
        self.assertAlmostEqual(self.frq.overview()["recorded_cloud_cost"], round(expected, 4))

    def test_the_estimate_is_shown_first_and_nothing_is_refused_for_its_cost(self):
        # Requirement, 2026-09-23: Faculty keeps no spending cap; the OpenRouter and Vercel accounts carry the limits.
        self.configure([endpoint(self.host, "alpha-vision", price_in=100.0, price_out=100.0)], max_job_cost=.5)  # A saved cap is retired.
        self.assertNotIn("max_job_cost", self.frq.settings()["settings"])
        preview = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": "cloud_standard"})
        self.assertGreater(preview["estimate"]["estimated_cost"], .5)
        self.assertNotIn("cap", preview["estimate"])
        self.assertEqual(preview["estimate"]["roles"]["primary"]["endpoint"], "alpha-vision")
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        self.assertGreater(self.frq.job(job["id"])["cost"], 0)  # Recorded, never a reason to stop.

    def test_a_rate_limit_a_server_error_and_a_rejected_request_are_confirmed_and_retried_only_after_being_recorded(self):
        failures = iter([("status", 429, {"Retry-After": "0"}), ("status", 500), ("status", 400)])
        self.host.script = lambda body, host: next(failures, None) or default_answer(body)
        self.configure(max_retries=4)
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        first = self.calls("step='reconciling'")
        self.assertEqual([(c["state"], json.loads(c["confirmation_json"] or "{}").get("status")) for c in first], [("failed", 429), ("failed", 500), ("failed", 400), ("completed", None)])
        for earlier, following in zip(first, first[1:]):  # The confirmation was on record before the next request left.
            self.assertLessEqual(earlier["finished_at"], following["submitted_at"])
        self.assertIn("scripted failure", json.loads(first[1]["confirmation_json"])["body"])

    def test_a_failure_that_repeats_stops_at_the_attempt_cap_and_waits_for_the_instructor(self):
        self.host.script = lambda body, host: ("status", 500)
        job = self.queue()
        self.run_all()
        failed = self.frq.job(job["id"])
        self.assertEqual((failed["status"], failed["progress"]["failed"]), ("failed", 1))
        self.assertIn("attempt cap", failed["tasks"][0]["error"])
        self.assertEqual([c["state"] for c in self.calls()], ["failed"] * 3)  # max_retries=3 in this fixture
        self.assertIsNone(self.frq.response(failed["tasks"][0]["response_id"])["result"])
        self.host.script = lambda body, host: default_answer(body)
        self.frq.retry_job(job["id"])  # One action retries every such response.
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")

    def test_no_credits_is_said_plainly_and_never_retried(self):
        self.host.script = lambda body, host: ("status", 402)
        job = self.queue()
        self.run_all()
        task = self.frq.job(job["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertRegex(task["error"], "no credits.*Retrying cannot fix this")
        self.assertEqual(len(self.calls()), 1)

    def test_a_rejected_key_keeps_only_the_error_type_and_code(self):
        # OPENAI DECISIONS / credentials (2026-10-09): OpenAI's invalid-key 401 names the key in masked form; the receipt keeps the
        # status and the error's type and code, never its message (the MC reader goes through this transport).
        rejected = {"error": {"message": "Incorrect API key provided: sk-proj-****abcd.", "type": "invalid_request_error", "code": "invalid_api_key"}}
        self.host.script = lambda body, host: ("status", 401, {}, rejected)
        job = self.queue()
        self.run_all()
        calls = self.calls()
        self.assertEqual(len(calls), 1)  # a rejected key is never resent
        confirmation = json.loads(calls[0]["confirmation_json"])
        self.assertEqual(confirmation, {"status": 401, "error": {"type": "invalid_request_error", "code": "invalid_api_key"}})
        with connect(self.app.config["DATABASE"]) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn("sk-proj", dump)
        self.assertEqual(self.frq.job(job["id"])["tasks"][0]["status"], "failed")

    def test_a_dropped_connection_is_an_unknown_outcome_resent_at_most_twice(self):
        self.host.script = lambda body, host: ("drop",)
        job = self.queue()
        self.run_all()
        task = self.frq.job(job["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertIn("unknown after 2 automatic resends", task["error"])
        self.assertEqual([(c["state"], c["confirmation_json"]) for c in self.calls()], [("unknown", None)] * 3)  # The request and two resends.
        self.assertEqual(len(self.host.requests), 3)

    def test_a_read_timeout_is_an_unknown_outcome_too(self):
        stalls = iter([("stall", 2.5)])
        self.host.script = lambda body, host: next(stalls, None) or default_answer(body)
        real = self.frq.cloud_provider
        with patch.object(self.frq, "cloud_provider", side_effect=lambda e, timeout=180: real(e, 1)):
            job = self.queue()
            self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        self.assertEqual([c["state"] for c in self.calls("step='reconciling'")], ["superseded", "completed"])  # The lost request can never be applied later.
        self.assertIn("unknown", self.calls("step='reconciling'")[0]["error"] + "unknown")

    def test_a_hosted_model_request_has_no_timeout_and_its_connection_keepalive(self):
        # A hosted answer arrives only when the model has finished writing: MiMo V2.6 Pro's rulings timed out at 180 seconds on
        # 2026-09-23, and so did every resend. What notices a dead connection instead is TCP keepalive, on the real socket.
        import socket
        from grading.providers import base
        timeouts, probes, real = set(), [], self.frq.cloud_provider
        def record(sock):
            original(sock)
            probes.append((sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE),
                           *(sock.getsockopt(socket.IPPROTO_TCP, getattr(socket, name)) for name in base.KEEPALIVE if hasattr(socket, name))))
        original = base.keepalive
        with patch.object(self.frq, "cloud_provider", side_effect=lambda e, timeout=180: timeouts.add((e["name"], timeout)) or real(e, timeout)), \
             patch.object(base, "keepalive", side_effect=record):
            job = self.queue()
            self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        self.assertEqual(timeouts, {("alpha-vision", None)})
        expected = (1, *(value for name, value in base.KEEPALIVE.items() if hasattr(socket, name)))
        self.assertTrue(probes)
        self.assertEqual(set(probes), {expected})

    def test_a_refused_connection_is_confirmed_because_nothing_was_sent(self):
        self.host.stop()
        job = self.queue()
        self.run_all()
        calls = self.calls()
        self.assertEqual({c["state"] for c in calls}, {"failed"})
        self.assertEqual(json.loads(calls[0]["confirmation_json"]), {"refused": "ConnectionRefusedError"})
        self.assertEqual(self.frq.job(job["id"])["status"], "failed")
        self.host = FakeHost().start()  # Satisfy the cleanup registered in setUp.

    def test_a_malformed_answer_is_asked_again_then_waits_for_review_and_never_scores(self):
        self.host.script = lambda body, host: ("raw", '{"transcript": ', "stop")
        job = self.queue()
        self.run_all()
        task = self.frq.job(job["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rejected_outputs").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_results").fetchone()[0], 0)

    def test_a_refused_answer_is_asked_again_with_a_little_sampling_because_temperature_zero_repeats_it(self):
        answers = iter([("raw", '{"transcript": "\\quad\\quad\\quad', "stop")])
        self.host.script = lambda body, host: next(answers, None) or default_answer(body)
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        reads = [r["body"]["temperature"] for r in self.host.requests if prompt_name(r["body"]) == "reconcile"]
        self.assertEqual(reads, [0, .3])
        self.assertEqual({r["body"]["temperature"] for r in self.host.requests if prompt_name(r["body"]) == "grade"}, {0})
        with connect(self.path) as db:
            step = json.loads(db.execute("SELECT provider_json FROM frq_steps WHERE stage='reconciling'").fetchone()[0])
        self.assertEqual(step["temperature"], .3)  # The receipt says how the accepted answer was sampled.

    def test_a_rectangle_without_area_is_dropped_like_no_rectangle_instead_of_costing_the_reading(self):
        from grading.models import transcript
        value = transcript({"plain_text": "x = 6", "latex": "x = 6", "confidence": .9, "ambiguities": [],
                            "lines": [{"id": "line-1", "elements": [{"type": "math", "text": "x = 6", "latex": "x = 6", "bbox": [.2, .3, .2, .3], "crop": 0, "confidence": .9}]}]})
        self.assertIsNone(value["lines"][0]["elements"][0]["bbox"])
        self.assertTrue(value["lines"][0]["elements"][0]["bbox_discarded"])

    def test_an_answer_cut_off_by_its_output_budget_is_paid_for_once_and_never_asked_again(self):
        # Seen live: a reviewer spent its whole budget thinking, six times in seven. The same request ends the same way, and each one is billed.
        self.configure([endpoint(self.host, "alpha-vision", price_in=1.0, price_out=2.0)])
        self.host.script = lambda body, host: ("raw", '{"transcript": ', "length")
        job = self.queue()
        self.run_all()
        state = self.frq.job(job["id"])
        self.assertEqual(state["tasks"][0]["status"], "failed")
        self.assertIn("whole output budget", state["tasks"][0]["error"])
        self.assertIn("raise this endpoint’s answer or reasoning budget", state["tasks"][0]["error"])
        calls = self.calls("step='reconciling'")
        self.assertEqual([c["state"] for c in calls], ["failed"])
        self.assertGreater(calls[0]["cost"], 0)  # A refused answer was still billed; the batch's spending says so.
        self.assertAlmostEqual(state["cost"], calls[0]["cost"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rejected_outputs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_results").fetchone()[0], 0)

    def test_a_thinking_model_has_a_budget_apart_from_its_answer_and_any_other_model_is_told_not_to_think(self):
        from grading.providers.cloud import HostedEndpoint
        request = {"prompt": "transcript_grade", "payload": {"rubric": {"items": []}, "transcript": {"lines": []}}, "images": []}
        thinker = HostedEndpoint(endpoint(self.host, "thinker", aggregator=True, reasoning=True, reasoning_tokens=6000, max_tokens=4000)).body(request)[0]
        self.assertEqual((thinker["max_tokens"], thinker["reasoning"]), (10000, {"max_tokens": 6000, "exclude": False}))
        plain = HostedEndpoint(endpoint(self.host, "plain", aggregator=True, max_tokens=4000)).body(request)[0]
        self.assertEqual((plain["max_tokens"], plain["reasoning"]), (4000, {"enabled": False}))
        # A direct host receives no field it may not know.
        self.assertNotIn("reasoning", HostedEndpoint(endpoint(self.host, "direct", max_tokens=4000)).body(request)[0])

    def test_many_responses_run_in_parallel_within_the_endpoint_limit(self):
        self.configure([endpoint(self.host, "alpha-vision", max_inflight=2)])
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=second), changed="second")
        questions = [self.qid]
        for key in ("q2", "q3"):
            plan = self.frq.assignment(self.plan["id"])
            plan = self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": key, "assignment_version_id": self.version_id,
                "content": {"title": key, "max_points": 10, "problem": "Solve", "regions": [{"page": 1, "region": {"x": .1, "y": .5, "width": .8, "height": .4}}]}})
            q = next(q for q in plan["questions"] if q["question_key"] == key)
            plan = self.frq.import_rubric(plan["id"], q["question_id"], {"version": q["version"], "source_text": "-1 Error", "rules": {"items": [{"id": "R1", "description": "Error", "points": -1}]}})
            q = next(q for q in plan["questions"] if q["question_key"] == key)
            self.frq.approve_rubric(plan["id"], q["question_id"], {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"]})
            finalize_rubrics(self.frq, plan["id"])
            questions.append(q["question_id"])
        self.host.script = lambda body, host: ("stall", .25)
        job = self.queue(questions=questions)
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual((done["status"], done["progress"]["completed"], done["progress"]["total"]), ("review_ready", 6, 6))
        self.assertEqual(self.host.peak, 2)  # Parallel, and never beyond the configured requests in flight.
        self.assertEqual(len(self.calls("state='completed'")), 12)
        with connect(self.path) as db:  # No response ends with two grades.
            self.assertEqual(db.execute("SELECT COUNT(*),COUNT(DISTINCT response_id) FROM frq_results").fetchone()[:], (6, 6))

    def test_the_response_that_is_furthest_along_runs_first_so_proposals_arrive_while_a_batch_is_running(self):
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=second), changed="second")
        job = self.queue()
        with connect(self.path) as db:
            tasks = [r[0] for r in db.execute("SELECT id FROM frq_tasks WHERE job_id=? ORDER BY rowid", (job["id"],))]
            db.execute("UPDATE frq_tasks SET lane='cloud',stage='reconciling',updated_at='2026-01-01T00:00:00+00:00' WHERE id=?", (tasks[0],))
            db.execute("UPDATE frq_tasks SET lane='cloud',stage='grading',updated_at='2026-01-02T00:00:00+00:00' WHERE id=?", (tasks[1],))
        claimed = self.frq._cloud_claim(1)
        self.assertEqual([(kind, item["id"]) for kind, item, _ in claimed], [("task", tasks[1])])

    def test_within_one_stage_the_response_with_more_steps_done_runs_first(self):
        # A 2,169-response batch (2026-09-24) cut every crop before any response finished cutting: the one that had just cut its crop
        # went to the back of the queue. The one further along within its stage now goes first, however recently it last ran.
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=second), changed="second")
        job = self.queue()
        with connect(self.path) as db:
            tasks = [r[0] for r in db.execute("SELECT id FROM frq_tasks WHERE job_id=? ORDER BY rowid", (job["id"],))]
            db.execute("UPDATE frq_tasks SET lane='cloud',stage='extracting',updated_at='2026-01-01T00:00:00+00:00' WHERE id=?", (tasks[0],))
            db.execute("UPDATE frq_tasks SET lane='cloud',stage='extracting',updated_at='2026-01-02T00:00:00+00:00' WHERE id=?", (tasks[1],))
            db.execute("INSERT INTO frq_steps VALUES(?,?,?,?,?,?,?,?,?,?)", ("step-crop", tasks[1], "crop:0", 0, "0" * 64, "{}", "0" * 64, "{}", 0.1, "2026-01-02T00:00:00+00:00"))
        claimed = self.frq._cloud_claim(1)
        self.assertEqual([(kind, item["id"]) for kind, item, _ in claimed], [("task", tasks[1])])

    def test_a_sample_grades_a_stable_handful_of_papers_per_question_before_a_whole_exam(self):
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=second), changed="second")
        everyone = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": "cloud_standard"})
        sampled = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": "cloud_standard", "sample": 1})
        self.assertEqual((len(everyone["eligible"]), len(sampled["eligible"]), sampled["estimate"]["responses"]), (2, 1, 1))
        again = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": "cloud_standard", "sample": 1})
        self.assertEqual(sampled["eligible"][0]["enrollment_id"], again["eligible"][0]["enrollment_id"])  # The same papers every time.
        job = self.queue(sample=1)
        self.assertEqual(job["progress"]["total"], 1)
        with self.assertRaises(ValueError):
            self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "sample": 0})

    def test_an_aggregator_request_pins_its_upstream_host_and_the_receipt_names_who_served_it(self):
        self.configure([endpoint(self.host, "alpha-vision", aggregator=True, upstream="DeepInfra")])
        self.host.upstream = "DeepInfra"
        self.queue()
        self.run_all()
        body = self.host.requests[0]["body"]
        self.assertEqual((body["provider"], body["usage"]), ({"order": ["DeepInfra"], "allow_fallbacks": False, "require_parameters": True}, {"include": True}))
        self.assertEqual({c["upstream"] for c in self.calls()}, {"DeepInfra"})

    def test_a_schema_a_host_finds_too_complex_is_sent_once_more_without_its_array_length_bounds(self):
        """Measured live 2026-09-21: Google refused every request for the two questions with 16 and 18 rubric lines (400
        INVALID_ARGUMENT, 125 unbilled refusals in one batch) once the evidence schema was valid; rubrics of 13 lines passed. Seven
        test requests pinned it to `minItems`/`maxItems`: without them the same request returned all 18 decisions with evidence."""
        from grading.providers.cloud import HostedEndpoint
        HostedEndpoint.bounds_refused.clear()
        self.addCleanup(HostedEndpoint.bounds_refused.clear)
        bounded = lambda body: '"minItems"' in json.dumps(body.get("response_format") or {})
        complaint = ("status", 400, {}, {"error": {"message": "Provider returned error", "code": 400,
            "metadata": {"raw": '{"error": {"code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT"}}', "provider_name": "A host"}}})
        self.host.script = lambda body, host: complaint if prompt_name(body) == "grade" and bounded(body) else default_answer(body)
        self.configure([endpoint(self.host, "alpha-vision")])
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        grades = [bounded(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "grade"]
        self.assertEqual(grades, [True, False])  # Refused with its bounds, then sent ONCE without them; the reader's request was never touched.
        self.assertEqual([(c["step"], c["state"]) for c in self.calls()], [("reconciling", "completed"), ("grade:primary", "completed")])  # The refusal ran nothing.
        with connect(self.path) as db:  # The receipt says what was withheld; the contract itself (the schema) was still sent.
            notes = [json.loads(r[0]).get("schema_note") for r in db.execute("SELECT provider_json FROM frq_steps WHERE stage IN ('grade:primary','grading') ORDER BY rowid")]
        self.assertTrue(any(note and "array length bounds withheld" in note for note in notes), notes)
        self.assertEqual(len(HostedEndpoint.bounds_refused), 1)
        # A 400 about anything else is not blamed on the bounds: one extra request, the lesson dropped, the FIRST refusal recorded.
        HostedEndpoint.bounds_refused.clear()
        self.host.requests.clear()
        self.host.script = lambda body, host: complaint
        with self.assertRaises(ProviderError) as refused:
            HostedEndpoint(endpoint(self.host, "alpha-vision")).submit({"prompt": "grade", "payload": {"rubric": {"items": [{"id": "R1"}]}}, "images": [], "durable": False})
        self.assertEqual((refused.exception.status, len(self.host.requests), HostedEndpoint.bounds_refused), (400, 2, {}))
        other = ("status", 400, {}, {"error": {"message": "Input text data may contain inappropriate content.", "code": 400}})
        self.host.requests.clear()
        self.host.script = lambda body, host: other
        with self.assertRaises(ProviderError):
            HostedEndpoint(endpoint(self.host, "alpha-vision")).submit({"prompt": "grade", "payload": {"rubric": {"items": [{"id": "R1"}]}}, "images": [], "durable": False})
        self.assertEqual(len(self.host.requests), 1)  # A refusal that does not name the schema is never sent twice.

    def test_an_empty_enum_member_a_host_refuses_is_dropped_instead_of_failing_every_response(self):
        """Measured live 2026-09-22: the Tag-Team reviewer offers `adopt: ""` (its prompt says to answer "" for "not adopting")
        and, with no flags, a `flag` enum of [""]. Google through the Vercel gateway answered 400 to EVERY such request -
        "response_schema.properties[adopt].enum[0]: cannot be empty" - 913 refusals in one run, five per response, and not one
        grade came out. Other hosts accept it, so the members are dropped only for the host that refuses them."""
        from grading.providers.cloud import HostedEndpoint
        HostedEndpoint.empty_enum_refused.clear()
        self.addCleanup(HostedEndpoint.empty_enum_refused.clear)
        schema_of = lambda body: json.dumps(((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {})
        complaint = ("status", 400, {}, {"error": {"message": "Provider returned error", "code": 400, "metadata": {"provider_name": "A host",
            "raw": '{"error":{"message":"* GenerateContentRequest.generation_config.response_schema.properties[adopt].enum[0]: cannot be empty\\n"}}'}}})
        request = {"prompt": "arbitrate", "images": [], "durable": False,
                   "payload": {"rubric": {"items": [{"id": "R1"}]}, "assessments": {"standard.primary": {}}, "flags": [], "standing": "standard.primary",
                               "answer_parts": [""], "transcript": {"lines": [{"id": "l1", "elements": [{"text": "x = 6"}]}]}}}
        # Refused while it carries "", accepted once the member is gone.
        self.host.script = lambda body, host: complaint if '""' in schema_of(body) else default_answer(body)
        result = HostedEndpoint(endpoint(self.host, "alpha-vision")).submit(request)
        self.assertEqual(result["state"], "completed")
        sent = [schema_of(r["body"]) for r in self.host.requests]
        self.assertEqual(len(sent), 2)                       # Refused once, then sent ONCE more without the empty members.
        self.assertIn('""', sent[0])
        self.assertNotIn('""', sent[1])
        self.assertIn('"standard.primary"', sent[1])         # The real labels stay: only "" was dropped.
        self.assertEqual(len(HostedEndpoint.empty_enum_refused), 1)
        self.assertIn("empty enum members withheld", result["result"]["metadata"]["enum_note"])
        # A 400 about anything else is never blamed on the empty member: one request, and the lesson is not kept.
        HostedEndpoint.empty_enum_refused.clear()
        self.host.requests.clear()
        self.host.script = lambda body, host: ("status", 400, {}, {"error": {"message": "Input text data may contain inappropriate content.", "code": 400}})
        with self.assertRaises(ProviderError):
            HostedEndpoint(endpoint(self.host, "alpha-vision")).submit(request)
        self.assertEqual((len(self.host.requests), HostedEndpoint.empty_enum_refused), (1, {}))

    def test_a_temperature_no_host_accepts_is_withheld_instead_of_refusing_every_request(self):
        """Seen live on 2026-09-21: not one host of openai/gpt-5.6-sol or anthropic/claude-fable-5.1 lists `temperature`, and the
        mandatory rubric check failed five unbilled times on each. Every host accepted `reasoning`, so the hint is not blamed."""
        from grading.providers.cloud import HostedEndpoint
        for memo in (HostedEndpoint.hint_refused, HostedEndpoint.temperature_refused):
            memo.clear()
            self.addCleanup(memo.clear)
        refusal = ("status", 404, {}, {"error": {"message": "No endpoints found that can handle the requested parameters.", "code": 404,
            "metadata": {"routing_funnel": [{"step": "Initial Endpoints", "endpoint_count": 7}], "failed_routing_step": "Filter by Parameters"}}})
        self.host.script = lambda body, host: refusal if "temperature" in body and body.get("provider", {}).get("require_parameters") else default_answer(body)
        self.configure([endpoint(self.host, "alpha-vision", aggregator=True)])
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        sent = [(prompt_name(r["body"]), "temperature" in r["body"], "reasoning" in r["body"]) for r in self.host.requests]
        # The hint is tried alone first and did not help, so it is kept; the temperature alone did, and the grader's request never carries one.
        self.assertEqual(sent, [("reconcile", True, True), ("reconcile", True, False), ("reconcile", False, True), ("grade", False, True)])
        self.assertEqual((HostedEndpoint.hint_refused, len(HostedEndpoint.temperature_refused)), ({}, 1))
        self.assertEqual([(c["step"], c["state"]) for c in self.calls()], [("reconciling", "completed"), ("grade:primary", "completed")])  # The refusals ran nothing.
        with connect(self.path) as db:  # The retained reading says what was sent for it.
            kept = [json.loads(row[0]) for row in db.execute("SELECT provider_json FROM frq_steps WHERE stage='reconciling'")]
        self.assertTrue(kept and all(step["temperature"] is None and "withheld" in step["temperature_note"] for step in kept), kept)
        # A model whose hosts take neither field gets both withheld, in at most three further unbilled requests.
        for memo in (HostedEndpoint.hint_refused, HostedEndpoint.temperature_refused):
            memo.clear()
        self.host.requests.clear()
        self.host.script = lambda body, host: refusal if ("temperature" in body or "reasoning" in body) else default_answer(body)
        request = {"prompt": "transcript_grade", "payload": {"rubric": {"items": []}, "transcript": {"lines": []}}, "images": []}
        HostedEndpoint(endpoint(self.host, "alpha-vision", aggregator=True)).submit(request)
        self.assertEqual([("temperature" in r["body"], "reasoning" in r["body"]) for r in self.host.requests], [(True, True), (True, False), (False, True), (False, False)])
        self.assertEqual((len(HostedEndpoint.hint_refused), len(HostedEndpoint.temperature_refused)), (1, 1))
        # The output contract and the instructor's token cap are requirements: they are never withheld, and nothing is remembered.
        for memo in (HostedEndpoint.hint_refused, HostedEndpoint.temperature_refused):
            memo.clear()
        self.host.requests.clear()
        self.host.script = lambda body, host: refusal
        with self.assertRaises(ProviderError) as refused:
            HostedEndpoint(endpoint(self.host, "alpha-vision", aggregator=True)).submit(request)
        self.assertTrue(all("response_format" in r["body"] and "max_tokens" in r["body"] for r in self.host.requests))
        self.assertEqual((refused.exception.status, len(self.host.requests), HostedEndpoint.hint_refused, HostedEndpoint.temperature_refused), (404, 4, {}, {}))
        # A host that is not an aggregator keeps its temperature whatever was learnt about a route.
        self.assertIn("temperature", HostedEndpoint(endpoint(self.host, "alpha-vision")).body(request)[0])

    def test_an_advisory_hint_no_host_accepts_is_withheld_instead_of_refusing_every_request(self):
        """Seen live on 2026-09-20: a router that requires every parameter found no host for a model whose hosts do not list `reasoning`."""
        from grading.providers.cloud import HostedEndpoint
        HostedEndpoint.hint_refused.clear()
        self.addCleanup(HostedEndpoint.hint_refused.clear)
        refusal = ("status", 404, {}, {"error": {"message": "No endpoints found that can handle the requested parameters.", "code": 404}})
        self.host.script = lambda body, host: refusal if "reasoning" in body and body.get("provider", {}).get("require_parameters") else default_answer(body)
        self.configure([endpoint(self.host, "alpha-vision", aggregator=True)])
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        sent = [(prompt_name(r["body"]), "reasoning" in r["body"], r["body"]["provider"]["require_parameters"]) for r in self.host.requests]
        # One refusal teaches the process: the same request goes again without the hint, and the grader's request never carries it.
        self.assertEqual(sent, [("reconcile", True, True), ("reconcile", False, True), ("grade", False, True)])
        calls = self.calls()
        self.assertEqual([(c["step"], c["state"]) for c in calls], [("reconciling", "completed"), ("grade:primary", "completed")])  # One call each: the refusal ran nothing.
        with connect(self.path) as db:  # The retained reading says what was sent for it.
            notes = [json.loads(row[0]).get("reasoning_hint", "") for row in db.execute("SELECT provider_json FROM frq_steps WHERE stage='reconciling'")]
        self.assertTrue(notes and all("withheld" in note for note in notes), notes)
        # Any other 404 stays a confirmed failure, and the hint is not blamed for it.
        HostedEndpoint.hint_refused.clear()
        self.host.requests.clear()
        self.host.script = lambda body, host: ("status", 404, {}, {"error": {"message": "No such model."}})
        request = {"prompt": "transcript_grade", "payload": {"rubric": {"items": []}, "transcript": {"lines": []}}, "images": []}
        with self.assertRaises(ProviderError) as refused:
            HostedEndpoint(endpoint(self.host, "alpha-vision", aggregator=True)).submit(request)
        self.assertEqual((refused.exception.status, refused.exception.confirmed, len(self.host.requests), HostedEndpoint.hint_refused), (404, True, 2, {}))
        # A host that is not an aggregator is never asked twice: it was sent no hint and no routing requirement.
        self.host.requests.clear()
        with self.assertRaises(ProviderError):
            HostedEndpoint(endpoint(self.host, "direct")).submit(request)
        self.assertEqual(len(self.host.requests), 1)

    def test_a_roles_reasoning_choice_is_sent_recorded_and_part_of_the_acceptance_scope(self):
        facts = {"supported": True, "mandatory": True, "efforts": ["low", "high", "max"], "default_effort": "max"}
        self.configure([endpoint(self.host, "alpha-vision", aggregator=True, max_tokens=4000, reasoning_tokens=6000, reasoning_options=facts)],
                       role_reasoning={"cloud_standard": {"primary": "high"}})
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        sent = {prompt_name(r["body"]): (r["body"]["reasoning"], r["body"]["max_tokens"]) for r in self.host.requests}
        # The reader made no choice and the model must think: the least it offers. The primary grader was asked to think harder.
        self.assertEqual(sent, {"reconcile": ({"effort": "low", "exclude": False}, 10000), "grade": ({"effort": "high", "exclude": False}, 10000)})
        self.assertEqual(len(self.host.requests), 2)  # Nothing was refused, so nothing was sent twice.
        with connect(self.path) as db:
            self.assertEqual(json.loads(db.execute("SELECT provider_json FROM frq_steps WHERE stage='reconciling'").fetchone()[0])["reasoning"], "low")
            task = dict(db.execute("SELECT * FROM frq_tasks").fetchone())
            question = self.frq.question(db, self.plan["id"], self.qid)
        batch = self.frq.job(job["id"])
        scope, _ = self.frq.acceptance_scope(task, batch, batch["profile"], question)
        self.assertEqual({role: e.get("reasoning") for role, e in scope["roles"].items()}, {"reader": "low", "primary": "high"})
        self.assertEqual(self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": "cloud_standard"})["estimate"]["roles"]["primary"]["reasoning"], "high")

    def test_a_model_that_cannot_be_told_not_to_think_is_given_its_reasoning_budget(self):
        """Seen live on 2026-09-20: a reviewer saved without the reasoning mark answered 400 to every request, five times per response."""
        from grading.providers.cloud import HostedEndpoint
        HostedEndpoint.hint_refused.clear()
        self.addCleanup(HostedEndpoint.hint_refused.clear)
        mandatory = ("status", 400, {}, {"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled.", "code": 400}})
        self.host.script = lambda body, host: mandatory if body.get("reasoning") == {"enabled": False} else default_answer(body)
        self.configure([endpoint(self.host, "alpha-vision", aggregator=True, max_tokens=4000, reasoning_tokens=6000)])
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        sent = [(prompt_name(r["body"]), r["body"].get("reasoning"), r["body"]["max_tokens"]) for r in self.host.requests]
        # One refusal teaches the process: the same request goes again as a reasoning model's, and so does every later one.
        thinks = ({"max_tokens": 6000, "exclude": False}, 10000)
        self.assertEqual(sent, [("reconcile", {"enabled": False}, 4000), ("reconcile", *thinks), ("grade", *thinks)])
        self.assertEqual([(c["step"], c["state"]) for c in self.calls()], [("reconciling", "completed"), ("grade:primary", "completed")])  # No failed call, no retry.
        with connect(self.path) as db:
            note, = [json.loads(row[0]).get("reasoning_hint", "") for row in db.execute("SELECT provider_json FROM frq_steps WHERE stage='reconciling'")]
        self.assertIn("reasoning is mandatory", note)
        # Any other 400 is not about the hint: it is sent once and stays a confirmed failure.
        HostedEndpoint.hint_refused.clear()
        self.host.requests.clear()
        self.host.script = lambda body, host: ("status", 400, {}, {"error": {"message": "Invalid request: the schema is too deep."}})
        request = {"prompt": "transcript_grade", "payload": {"rubric": {"items": []}, "transcript": {"lines": []}}, "images": []}
        with self.assertRaises(ProviderError) as refused:
            HostedEndpoint(endpoint(self.host, "alpha-vision", aggregator=True)).submit(request)
        self.assertEqual((refused.exception.status, len(self.host.requests), HostedEndpoint.hint_refused), (400, 1, {}))
        # An endpoint marked as a reasoning model never sends the hint, so it has nothing to learn.
        self.host.requests.clear()
        self.host.script = lambda body, host: mandatory if body.get("reasoning") == {"enabled": False} else default_answer(body)
        HostedEndpoint(endpoint(self.host, "thinker", aggregator=True, reasoning=True)).submit(request)
        self.assertEqual((len(self.host.requests), HostedEndpoint.hint_refused), (1, {}))

    def test_a_host_without_schema_constrained_output_relies_on_the_prompt_contract_and_backend_validation(self):
        self.configure([endpoint(self.host, "alpha-vision", structured="none")])
        self.host.script = lambda body, host: default_answer({**body, "response_format": {"json_schema": {"name": "reconcile" if "visual_confidence" in body["messages"][0]["content"].split("Output JSON schema")[0] else "grade"}}})
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        body = self.host.requests[0]["body"]
        self.assertNotIn("response_format", body)
        self.assertIn("Output JSON schema", body["messages"][0]["content"])

    def test_a_batch_api_job_is_recovered_by_its_id_after_a_restart_without_resending(self):
        self.configure([endpoint(self.host, "alpha-vision", batch_api=True)])
        self.host.batch_polls = 2
        job = self.queue()
        submitted = self.run_until_submitted()  # The reader request became a recorded batch job; the host is still working on it.
        self.assertEqual((submitted["state"], bool(submitted["provider_call_id"])), ("submitted", True))
        # Faculty restarts: a new service over the same catalog finds the task in its lane and the durable ID on the call.
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='running' WHERE job_id=?", (job["id"],))
        from grading.service import GradingService
        restarted = GradingService(self.frq.exams, self.frq.manual, self.frq.templates, self.frq.inbox)
        with patch.object(restarted, "provider", return_value=self.fake):
            self.run_all(service=restarted)
        self.assertEqual(restarted.job(job["id"])["status"], "review_ready")
        calls = self.calls()
        self.assertEqual([(c["step"], c["state"]) for c in calls], [("reconciling", "completed"), ("grade:primary", "completed")])
        self.assertEqual(sum(r["path"].endswith("/batches") for r in self.host.requests), 2)  # One batch per component; nothing was sent twice.

    def test_cancelling_a_batch_cancels_its_durable_provider_jobs_where_the_provider_permits(self):
        self.configure([endpoint(self.host, "alpha-vision", batch_api=True)])
        self.host.batch_polls = 99
        job = self.queue()
        self.run_until_submitted()
        self.frq.cancel_job(job["id"])
        self.assertEqual(len(self.host.cancelled), 1)
        self.assertEqual([c["state"] for c in self.calls()], ["cancelled"])
        self.assertFalse(self.frq.process_one())

    def test_a_restart_settles_the_interrupted_work_of_a_cancelled_batch_instead_of_queueing_it_for_ever(self):
        """Seen 2026-09-20: two batches were cancelled with reviewer requests in flight and the application restarted minutes
        later. Their 22 tasks went back to 'pending', which a cancelled batch is never claimed from, with 19 requests 'submitted'."""
        self.configure([endpoint(self.host, "alpha-vision")])
        job, other = self.queue(), None
        with connect(self.path) as db:
            # The dispatcher had sent the reader's request when the batch was cancelled; then the process died.
            task = db.execute("SELECT * FROM frq_tasks WHERE job_id=?", (job["id"],)).fetchone()
            db.execute("UPDATE frq_tasks SET status='running' WHERE id=?", (task["id"],))
            db.execute("""INSERT INTO frq_cloud_calls(id,task_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,request_sha256,prompt,prompt_sha256,state,submitted_at)
                VALUES('call-in-flight',?,?,'reconciling',0,'reader','cloud_standard','alpha-vision','fixture','fixture/alpha-vision','alpha','sha','reconcile','sha','submitted',?)""",
                (task["id"], job["id"], "2026-09-20T22:00:00+00:00"))
        self.frq.cancel_job(job["id"])  # Pending work is cancelled at once; work under way is left to finish.
        self.assertEqual(self.frq.job(job["id"])["progress"]["running"], 1)
        from grading.service import GradingService
        restarted = GradingService(self.frq.exams, self.frq.manual, self.frq.templates, self.frq.inbox)
        restarted._cloud_recover()
        done = restarted.job(job["id"])
        self.assertEqual((done["status"], done["progress"]["running"], done["progress"]["queued"], done["progress"]["cancelled"], done["progress"]["in_flight"]), ("cancelled", 0, 0, 1, 0))
        call, = self.calls()
        self.assertEqual(call["state"], "unknown")
        self.assertIn("its batch had been cancelled: nothing was sent again", call["error"])
        self.assertEqual(restarted.assignment(self.plan["id"])["workflow"]["pending"], 0)  # The Grade step no longer counts it as queued.
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='cancelled_work_settled'").fetchone()[0], 1)
        # Nothing of it is claimed or sent afterwards, and asking again changes nothing.
        restarted._cloud_recover()
        self.assertFalse(restarted.process_one())
        self.assertEqual(restarted._cloud_claim(4), [])
        self.assertEqual(len(self.host.requests), 0)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='cancelled_work_settled'").fetchone()[0], 1)
        # A request can outlive its task: a restart interrupts it, the task waits as pending for the request's own timeout, and the
        # batch is cancelled before anything collects it. Seen 2026-09-21: 18 such requests stayed 'submitted' for good.
        third = self.queue()
        with connect(self.path) as db:
            waiting = db.execute("SELECT * FROM frq_tasks WHERE job_id=?", (third["id"],)).fetchone()
            db.execute("""INSERT INTO frq_cloud_calls(id,task_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,request_sha256,prompt,prompt_sha256,state,submitted_at)
                VALUES('call-orphaned',?,?,'reconciling',0,'reader','cloud_standard','alpha-vision','fixture','fixture/alpha-vision','alpha','sha','reconcile','sha','submitted',?)""",
                (waiting["id"], third["id"], "2026-09-21T02:24:50+00:00"))
        self.frq.cancel_job(third["id"])
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute("SELECT state,error LIKE '%Cancelled with its batch%' FROM frq_cloud_calls WHERE id='call-orphaned'").fetchone()), ("unknown", 1))
            # One left over from before this rule is settled by the next start.
            db.execute("""INSERT INTO frq_cloud_calls(id,task_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,request_sha256,prompt,prompt_sha256,state,submitted_at)
                VALUES('call-left-over',?,?,'grade:primary',0,'primary','cloud_standard','alpha-vision','fixture','fixture/alpha-vision','alpha','sha','grade','sha','submitted',?)""",
                (waiting["id"], third["id"], "2026-09-21T02:24:51+00:00"))
        restarted._cloud_recover()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT state FROM frq_cloud_calls WHERE id='call-left-over'").fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_cloud_calls WHERE state='submitted'").fetchone()[0], 0)
        # A request whose response had already FAILED is settled too (2026-09-24: out of file handles, a component failed while
        # recording its own request); a response that is still waiting keeps its request for the ordinary replay.
        failed = self.queue()
        with connect(self.path) as db:
            broken = db.execute("SELECT * FROM frq_tasks WHERE job_id=?", (failed["id"],)).fetchone()
            db.execute("UPDATE frq_tasks SET status='failed' WHERE id=?", (broken["id"],))
            db.execute("""INSERT INTO frq_cloud_calls(id,task_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,request_sha256,prompt,prompt_sha256,state,submitted_at)
                VALUES('call-of-a-failed-response',?,?,'reconciling',0,'reader','cloud_standard','alpha-vision','fixture','fixture/alpha-vision','alpha','sha','reconcile','sha','submitted',?)""",
                (broken["id"], failed["id"], "2026-09-24T01:23:18+00:00"))
        restarted._cloud_recover()
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute("SELECT state,error LIKE '%no longer waiting for it%' FROM frq_cloud_calls WHERE id='call-of-a-failed-response'").fetchone()), ("unknown", 1))
        # A batch that was NOT cancelled keeps the old recovery: its interrupted work is queued again.
        other = self.queue()
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='running' WHERE job_id=?", (other["id"],))
        restarted._cloud_recover()
        self.assertEqual(restarted.job(other["id"])["progress"]["queued"], 1)

    def test_queued_runs_are_claimed_in_turn_put_back_by_a_restart_and_failed_in_their_own_words(self):
        # The rubric review, rubric draft, quality audit, flag review and the three quiz draft runs share the dispatcher's turns.
        kinds = (("rubric_quality", "frq_rubric_quality_checks", "run_rubric_quality", "The rubric review stopped unexpectedly. Request a new review."),
                 ("rubric_generation", "frq_rubric_generations", "run_rubric_generation", "The rubric draft stopped unexpectedly. Draft again."),
                 ("quality_audit", "frq_quality_audits", "run_quality_audit", "The quality audit stopped unexpectedly. Run it again."),
                 ("flag_resolution", "frq_flag_resolutions", "run_flag_resolution", "The flag review stopped unexpectedly. Run it again."),
                 ("quiz_revision", "quiz_draft_revisions", "run_quiz_revision", "The quiz draft stopped unexpectedly. Try again."),
                 ("quiz_approval", "quiz_draft_approvals", "run_quiz_approval", "Carrying out the approval stopped unexpectedly. Approve again: every step reuses what was made."),
                 ("quiz_rebuild", "quiz_draft_rebuilds", "run_quiz_rebuild", "The rebuild stopped unexpectedly. Rebuild again: every step reuses what was made."))

        def add(db, table, ident, status, not_before=""):
            values = {"id": ident, "status": status, "updated_at": "2026-01-01T00:00:00", "not_before": not_before, "error": ""}
            for column in db.execute(f"PRAGMA table_info({table})").fetchall():
                if column["name"] not in values and column["notnull"] and column["dflt_value"] is None:
                    values[column["name"]] = "{}" if column["name"].endswith("_json") else 1 if column["name"] == "number" else \
                        0 if (column["type"] or "").upper().startswith("INT") else f"{column['name']}-{ident}"
            db.execute(f"INSERT INTO {table}({','.join(values)}) VALUES({','.join('?' * len(values))})", list(values.values()))

        def states(db):
            return {table: {row[0]: row[1:] for row in db.execute(f"SELECT id,status,error FROM {table} WHERE id LIKE ? ORDER BY id", (f"{kind}-%",))} for kind, table, _, _ in kinds}

        with connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            for kind, table, _, _ in kinds:
                for ident, status, not_before in ((f"{kind}-1", "pending", ""), (f"{kind}-2", "pending", ""), (f"{kind}-later", "pending", "9999-01-01T00:00:00"), (f"{kind}-done", "complete", "")):
                    add(db, table, ident, status, not_before)
        # The rubric review is claimed first and alone; every other kind is claimed one at a time, within the capacity.
        claimed = self.frq._cloud_claim(3)
        self.assertEqual([(kind, item["id"]) for kind, item, _ in claimed], [("rubric_quality", "rubric_quality-1"), ("rubric_generation", "rubric_generation-1"), ("quality_audit", "quality_audit-1")])
        claimed = self.frq._cloud_claim(100)
        self.assertEqual([(kind, item["id"]) for kind, item, _ in claimed],
                         [("rubric_quality", "rubric_quality-2"), ("rubric_generation", "rubric_generation-2"), ("quality_audit", "quality_audit-2"),
                          ("flag_resolution", "flag_resolution-1"), ("quiz_revision", "quiz_revision-1"), ("quiz_approval", "quiz_approval-1"), ("quiz_rebuild", "quiz_rebuild-1")])
        early = ("rubric_quality", "rubric_generation", "quality_audit")  # Claimed twice; the others once. A later or finished run is never claimed.
        with connect(self.path) as db:
            for kind, table, _, _ in kinds:
                self.assertEqual({ident: row[0] for ident, row in states(db)[table].items()},
                                 {f"{kind}-1": "running", f"{kind}-2": "running" if kind in early else "pending", f"{kind}-later": "pending", f"{kind}-done": "complete"}, table)
        # A restart puts what was running back; a run that stops unexpectedly fails with words for its own kind, and only while running.
        self.frq._cloud_recover()
        with connect(self.path) as db:
            self.assertEqual({table: {row[0] for row in db.execute(f"SELECT id FROM {table} WHERE status='running'")} for _, table, _, _ in kinds}, {table: set() for _, table, _, _ in kinds})
            for kind, table, _, _ in kinds:
                db.execute(f"UPDATE {table} SET status='running' WHERE id=?", (f"{kind}-1",))
        for kind, table, method, text in kinds:
            with patch.object(type(self.frq), method, side_effect=RuntimeError("boom")), self.assertLogs("grading.cloud", level="ERROR"):
                self.frq._cloud_unit(kind, {"id": f"{kind}-1"}, None)
                self.frq._cloud_unit(kind, {"id": f"{kind}-done"}, None)
        with connect(self.path) as db:
            after = states(db)
        for kind, table, _, text in kinds:
            self.assertEqual(after[table][f"{kind}-1"], ("failed", text), table)
            self.assertEqual(after[table][f"{kind}-done"], ("complete", ""), table)
            self.assertEqual(after[table][f"{kind}-2"][0], "pending", table)

    def test_a_late_result_of_a_superseded_request_is_kept_as_a_receipt_and_never_applied(self):
        from grading.models import digest, encoded, plain_transcript, uid
        from course_storage import timestamp
        winner = {"transcript": plain_transcript("the request that won the race"), "visual_confidence": .9, "provider_response": {}}
        raced = []
        def script(body, host):
            if prompt_name(body) == "reconcile" and not raced:
                # While this request is in flight, a resend that raced it has its result applied first.
                raced.append(1)
                with connect(self.path) as db:
                    task = db.execute("SELECT * FROM frq_tasks").fetchone()
                    db.execute("INSERT INTO frq_steps VALUES(?,?,?,?,?,?,?,?,?,?)", (uid(), task["id"], "reconciling", task["attempt"], "0" * 64, encoded(winner), digest(winner), "{}", 0, timestamp()))
            return default_answer(body)
        self.host.script = script
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        late = self.calls("step='reconciling'")[0]
        self.assertEqual(late["state"], "superseded")
        self.assertEqual(json.loads(late["receipt_json"])["applied"], False)
        self.assertIn("transcript", json.loads(late["receipt_json"])["late_result"])
        with connect(self.path) as db:
            self.assertEqual([json.loads(r[0])["transcript"]["plain_text"] for r in db.execute("SELECT output_json FROM frq_steps WHERE stage='reconciling'")], ["the request that won the race"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_results").fetchone()[0], 1)  # No response ends with two grades.

    def test_several_named_endpoints_with_different_lineages_report_capabilities_and_health(self):
        self.host.served = ["fixture/alpha-vision", "fixture/beta-text"]
        listing = self.frq.endpoints()
        names = {e["name"]: e for e in listing["endpoints"]}
        self.assertEqual(set(names), {"nebius", "alpha-vision", "beta-text", "gamma-vision"})
        capabilities = names["beta-text"]["capabilities"]
        self.assertEqual((capabilities["image_input"], capabilities["lineage"], capabilities["schema_constrained_output"], capabilities["batch_api"], capabilities["durable_call_ids"]),
                         (False, "beta", True, False, False))
        for field in ("separated_reasoning", "max_inflight", "rate_limits", "price_per_million_input", "price_per_million_output"):
            self.assertIn(field, capabilities)
        self.assertEqual(names["alpha-vision"]["verification"], "unverified")  # An assumed catalog entry is not evidence.
        health = self.frq.health(refresh=True)["endpoints"]
        self.assertEqual({k: v["status"] for k, v in health.items()}, {"alpha-vision": "available", "beta-text": "available", "gamma-vision": "model_unavailable"})
        self.assertNotIn("key", json.dumps(listing).replace("key_variable", "").replace("key_present", ""))

    def test_the_capability_probe_records_what_an_endpoint_was_seen_to_do(self):
        self.frq.probe_endpoint("alpha-vision")
        self.frq.probe_endpoint("beta-text")
        self.assertTrue(self.frq.process_cloud())
        names = {e["name"]: e for e in self.frq.endpoints()["endpoints"]}
        seen = names["alpha-vision"]["probe"]["result"]
        self.assertEqual((names["alpha-vision"]["verification"], seen["structured_output"], seen["image_input"], seen["upstream"]), ("verified", True, True, "FakeUpstream"))
        self.assertEqual((names["beta-text"]["verification"], names["beta-text"]["probe"]["result"]["image_input"]), ("verified", None))
        self.assertTrue(all(not any(p["type"] == "image_url" for p in r["body"]["messages"][1]["content"]) if isinstance(r["body"]["messages"][1]["content"], list) else True
                            for r in self.host.requests if r["body"]["model"] == "fixture/beta-text"))
        self.configure([endpoint(self.host, "alpha-vision", model="fixture/changed")])
        self.assertEqual({e["name"]: e["verification"] for e in self.frq.endpoints()["endpoints"]}["alpha-vision"], "unverified")  # A changed endpoint is unverified again.

    def test_endpoint_settings_name_key_variables_and_never_hold_or_leak_a_key(self):
        bad = [{"key_env": "MATHPIX_APP_KEY"}, {"key_env": "FACULTY_SECRET_API_KEY"}, {"key_env": "PATH"}, {"base_url": "http://example.com/v1"},
               {"base_url": "https://user:secret@example.com/v1"}, {"name": "nebius"}, {"secret": "sk-live"}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ValueError):
                config.validate({"endpoints": [{**endpoint(self.host, "alpha-vision"), **change}]})
        with self.assertRaisesRegex(ValueError, "needs a vision endpoint"):
            self.configure([endpoint(self.host, "alpha-text", modality="text")])
        # Shared and unknown families are valid for every role; image requirements still apply.
        same = [endpoint(self.host, "alpha-vision"), endpoint(self.host, "alpha-large"), endpoint(self.host, "beta-text", modality="text")]
        tiers = lambda second, verifier: {tier: {"reader": "alpha-vision", "primary": "alpha-vision", "second": second, "verifier": verifier} for tier in config.TIERS}
        for second, verifier in (("alpha-large", ""), ("beta-text", "alpha-large")):
            config.validate({"endpoints": same, "tiers": tiers(second, verifier), "yardstick": ""})
            unknown = [{**e, "lineage": ""} for e in same]
            config.validate({"endpoints": unknown, "tiers": tiers(second, verifier), "yardstick": ""})
        self.assertEqual(config.validate({"endpoints": same, "tiers": tiers("beta-text", ""), "yardstick": ""})["tiers"]["cloud_standard"]["second"], "beta-text")
        defaults = config.validate({})  # The shipped defaults still retain model identities.
        lineage = {e["name"]: e["lineage"] for e in defaults["endpoints"]}
        for tier in defaults["tiers"].values():
            self.assertTrue(all(lineage[tier[role]] != lineage[tier["primary"]] for role in ("second", "verifier") if tier[role]))
        with patch.dict(os.environ, {"FIXTURE_API_KEY": "sk-never-stored"}):
            self.configure([endpoint(self.host, "alpha-vision", key_env="FIXTURE_API_KEY")])
            self.queue()
            self.run_all()
            self.assertEqual(self.host.requests[0]["authorization"], "Bearer sk-never-stored")
            self.assertTrue({e["name"]: e for e in self.frq.endpoints()["endpoints"]}["alpha-vision"]["key_present"])
        with connect(self.path) as db:
            dump = "\n".join(str(tuple(row)) for table in ("frq_settings", "frq_jobs", "frq_cloud_calls", "frq_steps", "frq_events") for row in db.execute(f"SELECT * FROM {table}"))
        self.assertNotIn("sk-never-stored", dump)
        self.assertIn("FIXTURE_API_KEY", dump)

    def test_the_application_stays_usable_with_every_cloud_provider_disabled_or_unreachable(self):
        self.host.stop()
        overview = self.frq.overview()
        self.assertEqual(overview["health"]["endpoints"]["alpha-vision"]["status"], "unavailable")
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize"}
        command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
        job = self.frq.create_job(self.plan["id"], command)
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")  # The local pipeline needs no cloud account.
        self.assertEqual(self.calls(), [])
        self.host = FakeHost().start()

    def test_the_built_in_nebius_connection_serves_a_cloud_role_and_its_own_mode_is_refused(self):
        # The Nebius key is bound to Nebius's hosts (`config.key_problem`); this test alone redirects its address to the fake host.
        with patch.dict(os.environ, {"NEBIUS_API_KEY": "fixture-secret", "NEBIUS_GRADING_URL": "https://nebius.invalid/v1"}), \
                patch("grading.config.hosted_url", side_effect=lambda value, label="Endpoint": value.rstrip("/")), \
                patch("grading.providers.cloud.key_problem", return_value=""):
            settings = self.frq.settings()
            tiers = settings["settings"]["tiers"]
            self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "cost_per_response": .01,
                "tiers": {**tiers, "cloud_standard": {**tiers["cloud_standard"], "second": "nebius"}}}})
            # The plain Nebius mode was removed on 2026-09-23: a request naming it is refused before anything is queued.
            with self.assertRaisesRegex(ValueError, "Nebius cloud was removed on 2026-09-23"):
                self.queue(mode="nebius")
            real = self.frq.cloud_provider
            # Same client, same records: only the address is redirected to the fake host for this test.
            with patch.object(self.frq, "cloud_provider", side_effect=lambda e, timeout=180: real({**e, "base_url": self.host.url} if e["name"] == "nebius" else e, timeout)):
                job = self.queue()
                self.run_all()
        self.assertEqual((self.frq.job(job["id"])["status"], self.frq.job(job["id"])["tier"]), ("review_ready", "cloud_standard"))
        nebius = [c for c in self.calls() if c["endpoint"] == "nebius"]
        self.assertEqual({(c["role"], c["tier"], c["requested_model"]) for c in nebius}, {("second", "cloud_standard", "Qwen/Qwen3-VL-30B-A3B-Instruct")})
        [request] = [r for r in self.host.requests if r["authorization"] == "Bearer fixture-secret"]
        self.assertEqual(request["body"]["response_format"], {"type": "json_object"})


class ApprovedCase(CloudCase):
    def approved(self, count=2):
        """Approve this many responses through the ordinary path, graded locally."""
        with connect(self.path) as db:
            others = [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id", (self.section["id"], self.enrollment))]
        for index in range(count - 1):
            self.reviewed_source(batch=self.ready_batch(enrollment_id=others[index]), changed=f"approved-{index}")
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize"}
        command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
        self.frq.create_job(self.plan["id"], command)
        self.run_all()
        for row in self.frq.responses(self.plan["id"]):
            self.approve(self.frq.response(row["id"]))  # R1 applied: 9 of 10 is the approved score.


class CanaryRemovedTests(ApprovedCase):
    def test_a_batch_grades_at_once_with_no_canary_and_saved_canary_settings_still_load(self):
        """"""
        self.approved(2)
        current = self.frq.settings()
        stored = {**current["settings"], "canary_enabled": True, "canary_size": 2, "canary_margin": .05}
        with connect(self.path) as db:  # Settings saved before the removal still carry its keys.
            db.execute("UPDATE frq_settings SET settings_json=? WHERE id=1", (json.dumps(stored),))
        settings = self.frq.settings()["settings"]
        self.assertFalse({"canary_enabled", "canary_size", "canary_margin"} & set(settings))
        preview = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": "cloud_standard"})
        self.assertNotIn("canary", preview)
        job = self.queue(operation="grade")
        self.assertEqual(job["canary"], [])
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual((done["status"], done["notice"]), ("review_ready", ""))
        self.assertEqual(self.calls("step='canary'"), [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_canary_runs").fetchone()[0], 0)


class DedicatedProviderTests(CloudCase):
    """The dedicated GPU provider behind the same interface (RunPod serverless shape), on the fake host: asynchronous, durable job IDs."""
    def setUp(self):
        super().setUp()
        self.configure([endpoint(self.host, "gpu-reader", kind="dedicated", profile="small")], dedicated_enabled=True)

    def test_submission_gives_a_durable_job_id_that_is_polled_to_a_result(self):
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        calls = self.calls()
        self.assertEqual([(c["provider"], c["state"], c["resource_profile"], bool(c["provider_call_id"])) for c in calls], [("dedicated", "completed", "small", True)] * 2)
        self.assertEqual(sum(r["path"].endswith("/run") for r in self.host.requests), 2)

    def test_a_transient_failure_retries_and_a_permanent_one_stops_at_the_cap(self):
        failures = iter([("status", 503, {"Retry-After": "0"})])
        self.host.script = lambda body, host: next(failures, None) or default_answer(body)
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        self.assertEqual(self.calls("step='reconciling'")[0]["state"], "failed")
        self.host.script = lambda body, host: ("status", 500)
        failed = self.queue()
        self.run_all()
        self.assertIn("attempt cap", self.frq.job(failed["id"])["tasks"][0]["error"])

    def test_a_job_the_provider_reports_failed_is_a_confirmed_failure_that_is_retried(self):
        job = self.queue()
        call = self.run_until_submitted()
        self.host.jobs[call["provider_call_id"]].update(status="FAILED", error="CUDA out of memory")
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        first, second = self.calls("step='reconciling'")
        self.assertEqual((first["state"], json.loads(first["confirmation_json"])["job_status"], second["state"]), ("failed", "FAILED", "completed"))

    def test_restart_recovers_by_job_id_and_a_result_is_never_applied_twice(self):
        job = self.queue()
        self.run_until_submitted()
        with connect(self.path) as db:  # The dispatcher dies while the provider works; the task is found in its lane afterwards.
            db.execute("UPDATE frq_tasks SET status='running' WHERE job_id=?", (job["id"],))
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        self.assertEqual(sum(r["path"].endswith("/run") for r in self.host.requests), 2)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_results").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_steps WHERE stage IN ('reconciling','grade:primary','grading')").fetchone()[0], 3)

    def test_the_dedicated_provider_is_refused_while_disabled(self):
        self.configure([endpoint(self.host, "gpu-reader", kind="dedicated", profile="small")], dedicated_enabled=False)
        with self.assertRaisesRegex(ValueError, "dedicated GPU provider is disabled"):
            self.queue()


class PolicyUnits(unittest.TestCase):
    def test_no_hosted_schema_constrains_a_number_with_an_enum(self):
        """Measured live 2026-09-21: Google's structured output takes enums of strings only. Given `crop: {"enum": [0]}` it dropped
        every field of the evidence object, and google/gemini-3.8-flash returned `[{}, {}]` for all 169 quotations of a batch."""
        from grading.schemas import SCHEMAS
        payload = {"rubric": {"items": [{"id": "R1"}, {"id": "R2"}]}}
        def enums(node, path=""):
            if isinstance(node, dict):
                if "enum" in node:
                    yield path, node["enum"]
                for key, value in node.items():
                    yield from enums(value, f"{path}/{key}")
            elif isinstance(node, list):
                for value in node:
                    yield from enums(value, path)
        for name in SCHEMAS:
            for crops in (0, 1, 3):
                schema = providers.grading_schema(name, payload, crops)
                for path, values in enums(schema):
                    self.assertTrue(all(isinstance(v, str) for v in values), f"{name}{path}: {values}")
        # The valid crops are still said, as a range; none without images. The backend refuses a crop that does not exist either way.
        crop = lambda name, n: providers.grading_schema(name, payload, n)["properties"]["applications"]["items"]["properties"]["evidence"]["items"]["properties"]["crop"]
        self.assertEqual(crop("adjudicate", 3), {"type": "integer", "minimum": 0, "maximum": 2})
        self.assertEqual(crop("grade", 1), {"type": "integer", "minimum": 0, "maximum": 0})
        self.assertEqual(crop("grade", 0), {"type": "integer", "minimum": 0})
        element = providers.grading_schema("reconcile", payload, 2)["properties"]["transcript"]["properties"]["lines"]["items"]["properties"]["elements"]["items"]["properties"]["crop"]
        self.assertEqual(element, {"type": "integer", "minimum": 0, "maximum": 1})

    def test_rubric_write_bounds_line_points_by_the_scoring_mode_and_the_printed_total(self):
        """Positive lines add credit up to the printed total, negative lines deduct down to minus it; unprinted, only the sign."""
        points = lambda payload: providers.grading_schema("rubric_write", payload, 1)["properties"]["items"]["items"]["properties"]["points"]
        problem = {"number": "18", "points": 10}
        self.assertEqual(points({"problem": problem}), {"type": "number", "minimum": 0, "maximum": 10})
        self.assertEqual(points({"problem": problem, "mode": "positive"}), {"type": "number", "minimum": 0, "maximum": 10})
        self.assertEqual(points({"problem": problem, "mode": "negative"}), {"type": "number", "minimum": -10, "maximum": 0})
        self.assertEqual(points({"problem": {**problem, "points": None}, "mode": "negative"}), {"type": "number", "maximum": 0})
        self.assertEqual(points({}), {"type": "number", "minimum": 0})

    def test_requests_in_flight_halve_on_a_rate_limit_and_recover_slowly(self):
        limiter = Limiter(16)
        limiter.rate_limited()
        self.assertEqual(limiter.limit, 8)
        for _ in range(20):
            limiter.succeeded()
        self.assertEqual(limiter.limit, 9)
        entered = []
        def hold():
            with limiter.slot():
                entered.append(1)
        threads = [threading.Thread(target=hold) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((len(entered), limiter.peak <= 9), (12, True))

    def test_a_provider_that_counts_requests_per_minute_is_paced_not_only_limited_in_flight(self):
        # Seen live: eight Mathpix reads in flight at two seconds each are 240 a minute, and the account allowed 200.
        from unittest.mock import patch
        clock = [1000.0]
        limiter = Limiter(8, per_minute=3)
        with patch("grading.cloud.time.monotonic", lambda: clock[0]):
            for _ in range(3):
                with limiter.slot():
                    pass
            self.assertTrue(limiter._paced(clock[0]))       # A fourth request would start inside the same minute.
            clock[0] += 61
            self.assertFalse(limiter._paced(clock[0]))      # A minute later the window is empty again.
        self.assertFalse(Limiter(8)._paced(0))               # Hosted endpoints are limited in flight only, as before.
        self.assertEqual(config.validate({**config.DEFAULTS})["mathpix_per_minute"], 800)

    def test_transport_classifies_what_is_known_about_an_outcome(self):
        from grading.providers.base import request_json, retry_after
        self.assertEqual(retry_after({"Retry-After": "7"}), 7.0)
        self.assertIsNone(retry_after({"Retry-After": "soon"}))
        self.assertIsNone(retry_after({}))
        with FakeHost(lambda body, host: ("status", 429, {"Retry-After": "3"})) as host:
            with self.assertRaises(ProviderError) as raised:
                request_json(host.url + "/chat/completions", body={"model": "m", "messages": []}, timeout=5)
        self.assertEqual((raised.exception.confirmed, raised.exception.status, raised.exception.retry_after, raised.exception.fixable), (True, 429, 3.0, True))
        with self.assertRaises(ProviderError) as refused:
            request_json(host.url + "/chat/completions", body={}, timeout=5)  # The host is stopped: nothing can be sent.
        self.assertTrue(refused.exception.confirmed)


if __name__ == "__main__":
    unittest.main()
