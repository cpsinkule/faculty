"""The Claude subscription connection (`grading/providers/agent_sdk.py`): bundles, their receipts, usage limits and a whole batch.

No subscription is used: `fake_claude.py` stands in for the Claude Code binary and answers like the fake OpenAI host.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from grading import config
from grading.providers import agent_sdk, bundles
from grading.providers.base import Abort, Cancelled, ProviderError, abortable
from grading.providers.cloud import provider
from rosters import connect
import fake_claude
from test_grading_cloud import CloudCase, endpoint as hosted

TESTS = Path(__file__).resolve().parent
PROBE_SCHEMA = {"type": "object", "properties": {"sum": {"type": "integer"}, "digits_seen": {"type": "string"}, "text": {"type": "string"}}, "required": ["sum", "digits_seen"]}


def subscription(name="claude-sub", **extra):
    return config.endpoint_entry({"name": name, "kind": "agent_sdk", "model": "claude-sonnet-5", "lineage": "claude", **extra}, saving=True)


def quick_bundles(case):
    """Bundles leave after a short quiet spell in tests, and no bundle or shrink carries over from another test."""
    for name, value in (("QUIET", .3), ("GATHER", 3)):
        patcher = patch.object(bundles, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    bundles.BundleMixin._open.clear()
    bundles.BundleMixin._shrunk.clear()


class FakeBinary(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.binary, self.control = fake_claude.install(folder.name, TESTS)
        for target, value in (("cli", lambda: self.binary), ("signed_in", lambda: True)):
            patcher = patch.object(agent_sdk, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        quick_bundles(self)
        agent_sdk.AgentSdkEndpoint._paused.clear()
        agent_sdk.AgentSdkEndpoint._busy.clear()

    def script(self, value):
        state = json.loads(self.control.read_text())
        self.control.write_text(json.dumps({**state, "script": value}))

    def invocations(self):
        log = Path(str(self.control) + ".log")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


class ConnectionSettings(unittest.TestCase):
    def test_a_subscription_connection_has_no_url_key_or_price_and_lets_in_one_call_per_session_of_bundles(self):
        e = subscription(bundle_size=5, sessions=3)
        self.assertEqual((e["base_url"], e["key_env"], e["price_in"], e["max_inflight"], e["structured"]), ("", "", 0.0, 15, "json_schema"))
        self.assertEqual(config.reasoning_choices(e), ["low", "medium", "high", "xhigh", "max"])
        with self.assertRaises(ValueError):
            subscription(base_url="https://example.com/v1")
        with self.assertRaises(ValueError):
            subscription(key_env="OPENROUTER_API_KEY")
        with self.assertRaises(ValueError):
            subscription(bundle_size=50)
        self.assertEqual((subscription(sessions=10)["sessions"], subscription(sessions=10, bundle_size=20)["max_inflight"]), (10, 200))
        self.assertEqual((subscription(sessions=256)["sessions"], subscription(sessions=256)["max_inflight"]), (256, 256))
        for wrong in (0, 257, 2.5):  # Concurrency is a whole number from 1 to 256.
            with self.assertRaises(ValueError):
                subscription(sessions=wrong)
        with self.assertRaises(ValueError):  # Sessions belong to the subscription; an API connection has requests in flight.
            config.endpoint_entry({**config.ENDPOINT, "name": "x", "base_url": "https://openrouter.ai/api/v1", "model": "a/b", "sessions": 2})
        with self.assertRaises(ValueError):
            config.endpoint_entry({**config.ENDPOINT, "name": "x", "base_url": "https://openrouter.ai/api/v1", "model": "a/b", "bundle_size": 21})

    def test_its_calls_cost_nothing_and_a_batch_using_it_is_not_unpriced(self):
        from grading.jobs import unpriced
        e = subscription()
        self.assertEqual(provider(e).cost({"prompt_tokens": 10})[0], 0.0)
        self.assertFalse(unpriced(e))


class Bundles(FakeBinary):
    def ask(self, bundle_provider, n, key=("job", "question", "grade:primary"), **extra):
        results = [None] * n

        def one(i):
            request = {"prompt": "probe", "payload": {"task": "Double it.", "double": i, "tag": f"t{i}"}, "images": [], "schema": PROBE_SCHEMA,
                       "bundle": {"key": list(key)}, **extra}
            try:
                results[i] = bundle_provider.submit(request)
            except (ProviderError, Cancelled) as error:
                results[i] = error
        threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return results

    def test_requests_of_one_question_and_step_arrive_together_and_leave_as_one_call_each_with_its_own_answer(self):
        results = self.ask(provider(subscription(bundle_size=4)), 4)
        self.assertEqual([r["result"]["output"]["sum"] for r in results], [0, 2, 4, 6])
        calls = self.invocations()
        self.assertEqual([(c["bundled"], c["items"]) for c in calls], [(True, 4)])
        self.assertFalse(calls[0]["api_key"])  # A plain environment: an API key would bill the API instead of the subscription.
        self.assertEqual(calls[0]["tools"], "")
        receipts = [r["result"] for r in results]
        self.assertEqual({r["metadata"]["bundle"]["id"] for r in receipts}.__len__(), 1)
        self.assertEqual(sorted(r["metadata"]["bundle"]["item"] for r in receipts), ["R1", "R2", "R3", "R4"])
        # Each receipt holds only its own answer, never another item's.
        for r in receipts:
            self.assertEqual(r["raw"]["answer"], r["output"])
            self.assertEqual(r["metadata"]["usage"]["billing"], "claude_subscription")

    def test_a_bundle_is_never_larger_than_its_setting_and_different_steps_never_share_one(self):
        results = self.ask(provider(subscription(bundle_size=2, sessions=4)), 5)
        self.assertTrue(all(isinstance(r, dict) for r in results))
        self.assertTrue(all(c["items"] <= 2 for c in self.invocations()))
        self.assertEqual(sum(c["items"] for c in self.invocations()), 5)
        before = len(self.invocations())
        p = provider(subscription(bundle_size=4))
        other = []
        threads = [threading.Thread(target=lambda k=k: other.append(self.ask(p, 1, key=("job", "question", k)))) for k in ("grade:primary", "grade:second")]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual([c["items"] for c in self.invocations()[before:]], [1, 1])

    def test_only_requests_of_the_same_question_and_version_share_a_call(self):
        p = provider(subscription(bundle_size=6))
        keys = [("job", "q-a", "A", "grade:primary"), ("job", "q-b", "B", "grade:primary"), ("job", "q-a", "B", "grade:primary")]
        results = [None] * 9

        def one(i):
            key = keys[i % 3]
            request = {"prompt": "probe", "payload": {"task": "Double it.", "double": i, "tag": "|".join(key[1:3])}, "images": [],
                       "schema": PROBE_SCHEMA, "bundle": {"key": list(key)}}
            results[i] = p.submit(request)
        threads = [threading.Thread(target=one, args=(i,)) for i in range(9)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual([r["result"]["output"]["sum"] for r in results], [2 * i for i in range(9)])
        calls = self.invocations()
        self.assertEqual(sorted(c["items"] for c in calls), [3, 3, 3])
        self.assertTrue(all(len(set(c["tags"])) == 1 for c in calls), calls)  # Never two questions, never two versions in one call.

    def test_an_item_missing_from_the_answer_is_that_response_s_own_refused_answer(self):
        self.script("drop:R2")
        results = self.ask(provider(subscription(bundle_size=3)), 3)
        failed = [r for r in results if isinstance(r, ProviderError)]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].confirmed)
        self.assertIsNotNone(failed[0].raw)  # Its receipt joins the response's refused answers, and it is asked again.
        self.assertEqual(sum(isinstance(r, dict) for r in results), 2)

    def test_a_reached_usage_limit_pauses_the_connection_until_the_reset_without_starting_claude_again(self):
        self.script("limit")
        results = self.ask(provider(subscription(bundle_size=2)), 2)
        self.assertTrue(all(isinstance(r, ProviderError) and r.status == 429 and r.retry_after > 3600 for r in results))
        count = len(self.invocations())
        self.script("")
        again = self.ask(provider(subscription(bundle_size=2)), 1)
        self.assertEqual((again[0].status, len(self.invocations())), (429, count))

    def test_a_provider_error_fails_every_member_as_a_confirmed_failure(self):
        self.script("error")
        results = self.ask(provider(subscription(bundle_size=2)), 2)
        self.assertTrue(all(isinstance(r, ProviderError) and r.confirmed and r.status == 529 for r in results))

    def test_a_request_without_a_bundle_goes_alone_as_its_own_call(self):
        answer = provider(subscription()).submit({"prompt": "probe", "payload": {"task": "Add 17 and 25."}, "images": [], "schema": PROBE_SCHEMA})
        self.assertEqual(answer["result"]["output"]["sum"], 42)
        self.assertEqual([(c["bundled"], c["items"]) for c in self.invocations()], [(False, 1)])

    def test_cancelling_a_waiting_member_sends_nothing_of_it(self):
        abort = Abort()
        abort.stop("cancelled")
        with abortable(abort), self.assertRaises(Cancelled):
            provider(subscription()).submit({"prompt": "probe", "payload": {"task": "x", "double": 1}, "images": [], "schema": PROBE_SCHEMA, "bundle": {"key": ["j", "q", "s"]}})
        self.assertEqual(self.invocations(), [])


class SubscriptionBatch(CloudCase, FakeBinary):
    def setUp(self):
        CloudCase.setUp(self)
        FakeBinary.setUp(self)

    def test_a_batch_graded_on_the_subscription_bundles_one_question_s_responses_and_keeps_a_call_record_per_response(self):
        e = subscription(bundle_size=4)
        self.configure([e])
        with connect(self.path) as db:
            others = [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment))][:2]
        for i, enrollment in enumerate(others):
            self.reviewed_source(batch=self.ready_batch(enrollment_id=enrollment), changed=f"other-{i}")
        job = self.queue()
        self.run_all()
        done = self.frq.job(job["id"])
        total = done["progress"]["total"]
        self.assertGreaterEqual(total, 2)
        self.assertEqual((done["status"], done["progress"]["completed"]), ("review_ready", total))
        calls = self.calls()
        self.assertTrue(all(c["state"] == "completed" and c["provider"] == "agent_sdk" and c["cost"] == 0 for c in calls),
                        [(c["step"], c["state"], c["error"], c["confirmation_json"], c["receipt_json"] and c["receipt_json"][:600]) for c in calls if c["state"] != "completed"])
        # Fewer Claude Code runs than requests: responses of the question went together.
        self.assertLess(len(self.invocations()), len(calls))
        self.assertTrue(any(c["bundled"] and c["items"] > 1 for c in self.invocations()))
        self.assertTrue(all(len(c["rubrics"]) == 1 for c in self.invocations()))  # One question's rubric per call.

    def test_responses_to_different_questions_of_one_batch_never_share_a_call(self):
        self.configure([subscription(bundle_size=6)])
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=second), changed="second")
        questions = [self.qid]
        for key in ("q2", "q3"):
            plan = self.frq.assignment(self.plan["id"])
            plan = self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": key, "assignment_version_id": self.version_id,
                "content": {"title": key, "max_points": 10, "problem": "Solve", "regions": [{"page": 1, "region": {"x": .1, "y": .5, "width": .8, "height": .4}}]}})
            q = next(q for q in plan["questions"] if q["question_key"] == key)
            plan = self.frq.import_rubric(plan["id"], q["question_id"], {"version": q["version"], "source_text": f"-1 Error {key}",
                "rules": {"items": [{"id": "R1", "description": f"Error in {key}", "points": -1}]}})
            q = next(q for q in plan["questions"] if q["question_key"] == key)
            self.frq.approve_rubric(plan["id"], q["question_id"], {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"]})
            questions.append(q["question_id"])
        job = self.queue(questions=questions)
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual((done["status"], done["progress"]["completed"], done["progress"]["total"]), ("review_ready", 6, 6))
        calls = [c for c in self.invocations() if c["bundled"]]
        self.assertTrue(calls)
        self.assertTrue(all(len(c["rubrics"]) == 1 for c in calls), calls)

    def test_a_reached_usage_limit_waits_for_the_reset_and_leaves_the_bundles_their_size(self):
        e = subscription(bundle_size=4)
        self.configure([e])
        self.script("limit")
        job = self.queue()
        for _ in range(6):
            self.frq.process_one()
        limited = self.calls("state='failed'")
        self.assertTrue(limited)
        self.assertTrue(all(json.loads(c["confirmation_json"]).get("usage_limit") for c in limited))
        self.assertEqual(self.frq._limiters[e["name"]].limit, e["max_inflight"])  # Never halved: the pause is the wait.
        with connect(self.path) as db:  # The response waits for the reset instead of failing.
            waiting = db.execute("SELECT status,not_before FROM frq_tasks WHERE job_id=?", (job["id"],)).fetchall()
        self.assertTrue(all(row[0] == "pending" and row[1] for row in waiting))

    def test_usage_limits_never_reach_the_busy_ceiling_however_long_the_reset_takes(self):
        self.configure([subscription(bundle_size=4)])
        self.script("limit")
        job = self.queue()
        for _ in range(40):  # Far past max_retries × 10 (30 in this fixture): a reset hours away wakes the task many times.
            agent_sdk.AgentSdkEndpoint._paused.clear()
            with connect(self.path) as db:
                db.execute("UPDATE frq_tasks SET not_before='' WHERE job_id=?", (job["id"],))
            self.frq.process_one()
        self.assertGreater(len(self.calls("state='failed'")), 30)
        with connect(self.path) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT status FROM frq_tasks WHERE job_id=?", (job["id"],))}, {"pending"})
        agent_sdk.AgentSdkEndpoint._paused.clear()
        self.script("")
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET not_before='' WHERE job_id=?", (job["id"],))
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")


class ApiBundles(CloudCase):
    """The same bundling on an OpenRouter/Vercel-style connection: one chat request for several responses of one question."""
    def setUp(self):
        CloudCase.setUp(self)
        quick_bundles(self)
        bundles.BundleMixin._sessions.clear()

    def second_paper(self, count=2):
        with connect(self.path) as db:
            others = [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment))][:count]
        for i, enrollment in enumerate(others):
            self.reviewed_source(batch=self.ready_batch(enrollment_id=enrollment), changed=f"other-{i}")

    def test_one_response_per_request_stays_the_default_and_is_not_stored(self):
        e = config.endpoint_entry(hosted(self.host, "alpha-vision"))
        self.assertNotIn("bundle_size", e)
        self.assertNotIn("bundle_size", config.endpoint_entry({**hosted(self.host, "alpha-vision"), "bundle_size": 1}))
        with self.assertRaises(ValueError):
            config.endpoint_entry({**hosted(self.host, "alpha-vision"), "sessions": 2})

    def test_a_batch_on_an_api_connection_bundles_one_question_s_responses_and_splits_usage_and_cost(self):
        self.configure([hosted(self.host, "alpha-vision", bundle_size=4, max_inflight=2, price_in=1.0, price_out=2.0)])
        self.second_paper()
        job = self.queue()
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual((done["status"], done["progress"]["completed"]), ("review_ready", done["progress"]["total"]))
        bundled = [r for r in self.host.requests if "BUNDLED REQUEST" in str(r["body"]["messages"][0]["content"])]
        self.assertTrue(bundled)
        self.assertLess(len(self.host.requests), len(self.calls()))  # Fewer requests than responses asked about.
        for request in bundled:
            system = request["body"]["messages"][0]["content"]
            self.assertIn("SAME question", system)
            self.assertEqual(request["body"]["response_format"]["json_schema"]["schema"]["required"], ["results"])
        calls = self.calls()
        self.assertTrue(all(c["state"] == "completed" for c in calls), [(c["step"], c["state"], c["error"], c["confirmation_json"]) for c in calls if c["state"] != "completed"])
        members = [json.loads(c["usage_json"]) for c in calls if json.loads(c["usage_json"]).get("bundle_size")]
        self.assertTrue(members)

    def test_an_answer_cut_off_in_a_bundle_is_asked_again_in_smaller_bundles(self):
        self.configure([hosted(self.host, "alpha-vision", bundle_size=4, max_inflight=2)])
        self.second_paper()
        state = {"cut": 0}

        def script(body, host):
            if "BUNDLED REQUEST" in str(body["messages"][0]["content"]) and not state["cut"]:
                state["cut"] += 1
                return ("raw", '{"results": [', "length")
            return default_answer(body)
        from fake_openai import default_answer
        self.host.script = script
        job = self.queue()
        self.run_all()
        done = self.frq.job(job["id"])
        self.assertEqual(state["cut"], 1)
        self.assertEqual((done["status"], done["progress"]["completed"]), ("review_ready", done["progress"]["total"]))


class WhoseAnswer(unittest.TestCase):
    """An answer filed under the wrong response is recognised by its content, not by its label (`bundles.matches`)."""
    sources = [bundles.content_words({"lines": [{"text": "x = 17 so the slope is 4 and the intercept is 9, giving the line four x plus nine"}]}),
               bundles.content_words({"lines": [{"text": "y = 23 because the matrix is singular with rank 2"}]}),
               bundles.content_words({"lines": [{"text": "x = 17 so the slope is 5 and the intercept is 11"}]})]

    def test_an_answer_about_its_own_response_is_its_own(self):
        self.assertEqual(bundles.matches({"evidence": [{"student_text": "slope is 4"}, {"student_text": "intercept is 9"}, {"student_text": "giving the line four x plus nine"}]}, self.sources, 0)["verdict"], "own")

    def test_an_answer_about_another_response_is_refused(self):
        found = bundles.matches({"evidence": [{"student_text": "the matrix is singular"}, {"student_text": "with rank 2"}, {"student_text": "y = 23 because"}]}, self.sources, 0)
        self.assertEqual(found["verdict"], "other")

    def test_a_grader_s_own_explanation_is_not_evidence_of_whose_answer_it_is(self):
        """The second trial batch's refusal, replayed: the grader's remark matched other students' layout notes by chance."""
        sources = [bundles.content_words({"lines": [{"text": "(3x4) - (3x2) subtract", "notes": "top margin"}, {"text": "$7 is their break-even point."}]}),
                   bundles.content_words({"lines": [{"text": "of 3x4 and I is 3x2"}], "layout": "top margin text from price"}),
                   bundles.content_words({"lines": [{"text": "(40000, 7)"}]})]
        answer = {"interpretation": {"final_answers": [{"text": "$7"}], "meaning": "Student states $7 is the break-even point; top margin text is from another question."},
                  "applications": [{"reason": "Gave price $7 as break-even point.", "evidence": [{"student_text": "$7 is their break-even point."}]}]}
        self.assertNotEqual(bundles.matches(answer, sources, 0)["verdict"], "other")
        self.assertEqual(bundles.content_words({"type": "text", "reason": "top margin", "text": "x = 4"}), {"4"})

    def test_the_correct_values_a_grader_explains_with_are_not_another_student_s(self):
        """The first trial batch's two refusals, replayed: each answer was about its own response."""
        sources = [bundles.content_words({"text": "P(x)= 76(34x) 19,000"}), bundles.content_words({"text": "P(x)=76x-(34x+19000) P(x)=42x-19000"}),
                   bundles.content_words({"text": "y = 34/76 item/dollar + 19,000"})]
        shared = bundles.words({"solution": "P(x) = 76x - (34x + 19000) = 42x - 19000", "problem": "Kayaks sell for $76 and cost $34 each plus $19,000"})
        answer = {"final_answers": [{"text": "P(x)= 76(34x) 19,000"}], "reason": "The correct profit is 42x - 19000; the student multiplied 76 by 34x, missing the operator."}
        self.assertNotEqual(bundles.matches(answer, sources, 0, shared)["verdict"], "other")
        self.assertEqual(bundles.words({"t": "19,000 and 1,234,567"}), {"19000", "and", "1234567"})

    def test_identifiers_are_not_evidence(self):
        self.assertEqual(bundles.words({"response_id": "4e845a30-13ef-4dad-acac-3d86273948c6", "lines": [{"id": "l1", "text": "x = 4"}],
                                        "note": "see 4e845a30-13ef-4dad-acac-3d86273948c6"}), {"4", "see"})

    def test_too_little_that_tells_the_responses_apart_proves_nothing(self):
        self.assertEqual(bundles.matches({"evidence": [{"student_text": "x = 17"}]}, self.sources, 0)["verdict"], "unknown")
        same = [bundles.content_words({"text": "the same work"})] * 2
        self.assertEqual(bundles.matches({"text": "the same work"}, same, 1)["verdict"], "unknown")

    def test_a_request_with_no_text_of_its_own_is_never_bundled(self):
        e = subscription(bundle_size=4)
        self.assertFalse(provider(e).bundled({"prompt": "read", "payload": {}, "images": [b"png"], "bundle": {"key": ["j", "q", "A", "s"]}}))
        self.assertTrue(provider(e).bundled({"prompt": "grade", "payload": {"transcript": "x = 4"}, "images": [], "bundle": {"key": ["j", "q", "A", "s"]}}))


class SwappedAnswers(unittest.TestCase):
    """A host that files two answers under each other's labels: both are refused, the third stands."""
    def setUp(self):
        from fake_openai import FakeHost, bundle_items
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        quick_bundles(self)
        bundles.BundleMixin._sessions.clear()

        def script(body, host):
            items = bundle_items(body)
            answers = [{"sum": 0, "digits_seen": "", "text": json.loads(single["messages"][1]["content"][0]["text"])["transcript"]} for _, single in items]
            if len(answers) > 2:
                answers[0], answers[1] = answers[1], answers[0]
            return {"results": [{"item": label, "answer": answer} for (label, _), answer in zip(items, answers)]}
        self.host.script = script

    def test_both_swapped_answers_are_refused_and_the_rest_stand(self):
        e = config.endpoint_entry({**hosted(self.host, "alpha-vision"), "bundle_size": 3})
        p = provider(e)
        papers = ["the slope is 4 and the intercept is 9 so y equals four x plus nine",
                  "the matrix is singular with rank 2 so no inverse exists at all",
                  "the derivative is 6x squared and the tangent line meets at 12"]
        results = [None] * 3

        def one(i):
            try:
                results[i] = p.submit({"prompt": "probe", "payload": {"task": "Report the transcript.", "transcript": papers[i], "lines": [{"text": papers[i]}]}, "images": [],
                                       "schema": PROBE_SCHEMA, "bundle": {"key": ["job", "q", "A", "grade:primary"]}})
            except ProviderError as error:
                results[i] = error
        threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(len(self.host.requests), 1)
        refused = [r for r in results if isinstance(r, ProviderError)]
        self.assertEqual(len(refused), 2)
        self.assertTrue(all(r.confirmation.get("swapped") and r.raw is not None for r in refused))
        kept = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["result"]["metadata"]["bundle"]["check"]["verdict"], "own")
        self.assertEqual(kept[0]["result"]["output"]["text"], papers[results.index(kept[0])])


class DispatcherRoom(CloudCase):
    def test_with_bundling_the_dispatcher_counts_requests_so_256_bundled_requests_can_fill(self):
        """Every response waiting to join a bundle is a component; without this, 4 per call allowed only 64 requests."""
        self.configure([hosted(self.host, "alpha-vision", bundle_size=4, max_inflight=256)], concurrency=256)
        seen = []
        with patch.object(type(self.frq), "_cloud_claim", lambda frq, capacity: seen.append(capacity) or []):
            self.frq.process_cloud()
        self.assertEqual(seen[0], 1024)
        self.configure([hosted(self.host, "alpha-vision", max_inflight=256)], concurrency=256)
        seen.clear()
        with patch.object(type(self.frq), "_cloud_claim", lambda frq, capacity: seen.append(capacity) or []):
            self.frq.process_cloud()
        self.assertEqual(seen[0], 256)  # Without bundling, exactly as before.


class ChatGptSubscription(unittest.TestCase):
    """The ChatGPT subscription through Codex (`providers.codex_sdk`): the same connection, another binary."""
    def setUp(self):
        from grading.providers import codex_sdk
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        import fake_codex
        self.binary, self.control = fake_codex.install(folder.name, TESTS)
        for target, value in (("cli", lambda: self.binary), ("signed_in", lambda: True)):
            patcher = patch.object(codex_sdk, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        quick_bundles(self)
        agent_sdk.AgentSdkEndpoint._paused.clear()
        agent_sdk.AgentSdkEndpoint._busy.clear()

    def endpoint(self, **extra):
        return config.endpoint_entry({"name": "chatgpt", "kind": "codex_sdk", "model": "gpt-6-astra", **extra}, saving=True)

    def runs(self):
        log = Path(str(self.control) + ".log")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_a_connection_takes_its_model_s_own_reasoning_levels_and_never_ultra(self):
        self.assertEqual(config.reasoning_choices(self.endpoint()), ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(config.reasoning_choices(self.endpoint(model="gpt-5.5")), ["low", "medium", "high", "xhigh"])
        self.assertEqual(provider(self.endpoint()).cost({})[0], 0.0)
        with self.assertRaises(ValueError):
            self.endpoint(model="claude-sonnet-5")

    def test_a_call_runs_with_every_tool_off_read_only_no_saved_session_and_no_api_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "should-never-pass"}):
            answer = provider(self.endpoint()).submit({"prompt": "probe", "payload": {"task": "Add 17 and 25."}, "images": [b"png"], "schema": PROBE_SCHEMA})
        self.assertEqual(answer["result"]["output"]["sum"], 42)
        run = self.runs()[0]
        self.assertFalse(run["api_key"])
        self.assertEqual(run["sandbox"], ["read-only"])
        self.assertTrue(run["ephemeral"] and run["ignore_user_config"])
        for tool in ("apps", "shell_tool", "computer_use", "browser_use", "plugins", "multi_agent", "image_generation"):
            self.assertIn(tool, run["disabled"])
        self.assertIn('web_search="disabled"', run["config"])
        self.assertIn('model_reasoning_effort="medium"', run["config"])
        self.assertEqual(answer["result"]["metadata"]["usage"]["billing"], "chatgpt_subscription")

    def test_a_bundle_marks_each_response_s_images_and_splits_the_answers(self):
        p, results = provider(self.endpoint(bundle_size=3)), [None] * 3

        def one(i):
            results[i] = p.submit({"prompt": "probe", "payload": {"task": "Double it.", "double": i}, "images": [b"png"] * (i + 1), "schema": PROBE_SCHEMA,
                                   "bundle": {"key": ["job", "q", "A", "grade:primary"]}})
        threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual([r["result"]["output"]["sum"] for r in results], [0, 2, 4])
        run = self.runs()[0]
        self.assertEqual((run["bundled"], run["items"], run["images"]), (True, 3, 6))
        self.assertEqual(sorted(run["markers"]), [1, 2, 3])  # Each response's images are marked in its own place.
        # Each response saw exactly its own images.
        self.assertEqual([r["result"]["output"]["digits_seen"] for r in results], ["1", "2", "3"])

    def test_a_reached_usage_limit_pauses_the_connection_for_the_time_it_names(self):
        state = json.loads(self.control.read_text())
        self.control.write_text(json.dumps({**state, "script": "limit"}))
        with self.assertRaises(ProviderError) as caught:
            provider(self.endpoint()).submit({"prompt": "probe", "payload": {"task": "x"}, "images": [], "schema": PROBE_SCHEMA})
        self.assertEqual(caught.exception.status, 429)
        self.assertTrue(caught.exception.confirmation["usage_limit"])
        self.assertGreater(caught.exception.retry_after, 2 * 3600)
        count = len(self.runs())
        with self.assertRaises(ProviderError):
            provider(self.endpoint()).submit({"prompt": "probe", "payload": {"task": "x"}, "images": [], "schema": PROBE_SCHEMA})
        self.assertEqual(len(self.runs()), count)  # Paused: Codex is not started again.
        # The Claude subscription is another plan: its connections are not paused by this.
        self.assertFalse(any(key[0] == "agent_sdk" for key in agent_sdk.AgentSdkEndpoint._paused))


class CapacityWaits(unittest.TestCase):
    """"Selected model is at capacity" is a busy service: waited for and asked again, never a failure."""
    def setUp(self):
        ChatGptSubscription.setUp(self)

    endpoint = ChatGptSubscription.endpoint
    runs = ChatGptSubscription.runs

    def test_a_model_at_capacity_is_a_busy_host_to_wait_for_not_a_failure(self):
        state = json.loads(self.control.read_text())
        self.control.write_text(json.dumps({**state, "script": "capacity"}))
        with self.assertRaises(ProviderError) as caught:
            provider(self.endpoint()).submit({"prompt": "probe", "payload": {"task": "x"}, "images": [], "schema": PROBE_SCHEMA})
        error = caught.exception
        self.assertEqual((error.status, error.confirmed, error.fixable, error.retry_after), (529, True, True, 30))
        self.assertTrue(error.confirmation["capacity"])
        # The cloud lane waits and asks again, and does not halve the connection's in-flight limit.
        from grading.cloud import CloudMixin, Limiter, RetryLater
        lane, limiter = CloudMixin(), Limiter(12)
        lane.rosters, lane.path = type("R", (), {"lock": lambda self: __import__("contextlib").nullcontext()})(), None
        with patch("grading.cloud.connect") as db, self.assertRaises(RetryLater) as waited:
            db.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
            lane._settle_failure("call", error, 1.0, limiter, 0, {"unknown_resends": 2}, {"step": "grade:flag-review"}, provider(self.endpoint()))
        self.assertEqual((waited.exception.seconds, limiter.limit), (30, 12))

    def test_capacity_backs_off_by_doubling_for_the_whole_connection_and_a_success_starts_over(self):
        request = {"prompt": "probe", "payload": {"task": "x"}, "images": [], "schema": PROBE_SCHEMA}
        key = ("codex_sdk", "gpt-6-astra")
        state = json.loads(self.control.read_text())
        self.control.write_text(json.dumps({**state, "script": "capacity"}))
        waits = []
        for _ in range(7):
            with self.assertRaises(ProviderError) as caught:
                provider(self.endpoint()).submit(request)
            waits.append(caught.exception.retry_after)
            count = len(self.runs())
            # Until the wait ends nothing is sent, and a request in the wait does not double it again.
            with self.assertRaises(ProviderError) as held:
                provider(self.endpoint()).submit(request)
            self.assertEqual((len(self.runs()), held.exception.status, held.exception.confirmation.get("not_sent")), (count, 529, True))
            self.assertLessEqual(held.exception.retry_after, caught.exception.retry_after)
            until, step = agent_sdk.AgentSdkEndpoint._busy[key]
            agent_sdk.AgentSdkEndpoint._busy[key] = (0, step)  # The wait is over.
        self.assertEqual(waits, [30, 60, 120, 240, 480, 900, 900])
        self.control.write_text(json.dumps({**state, "script": ""}))
        provider(self.endpoint()).submit(request)
        self.assertNotIn(key, agent_sdk.AgentSdkEndpoint._busy)
        self.control.write_text(json.dumps({**state, "script": "capacity"}))
        with self.assertRaises(ProviderError) as caught:
            provider(self.endpoint()).submit(request)
        self.assertEqual(caught.exception.retry_after, 30)


class PlanModels(unittest.TestCase):
    """Browse lists the models each plan's installed program offers now (`model_catalog.subscription_listing`; requirement, 2026-10-01), with the built-in lists standing in when the program cannot say."""
    def setUp(self):
        from grading.providers import codex_sdk
        import fake_codex
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.claude, self.claude_control = fake_claude.install(folder.name, TESTS)
        self.codex, self.codex_control = fake_codex.install(folder.name, TESTS)
        for module, binary in ((agent_sdk, lambda: self.claude), (codex_sdk, lambda: self.codex)):
            for target, value in (("cli", binary), ("signed_in", lambda: True)):
                patcher = patch.object(module, target, value)
                patcher.start()
                self.addCleanup(patcher.stop)

    @staticmethod
    def listing(control):
        log = Path(str(control) + ".log")
        return [run for run in map(json.loads, log.read_text().splitlines()) if run.get("listing")] if log.exists() else []

    @staticmethod
    def stopped(pid):
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(.05)
        return False

    def test_claude_code_names_its_models_now_each_with_its_own_effort_levels(self):
        from grading.model_catalog import subscription_listing
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "should-never-pass"}):
            listed = subscription_listing("claude")
        self.assertFalse(listed["stale"])
        self.assertEqual(listed["notice"], "")
        found = {m["id"]: m for m in listed["models"]}
        # The "default" alias is the Opus row, named by that row; what a Claude connection cannot name is left out.
        self.assertEqual(list(found), ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-sonnet-4-6"])
        self.assertEqual((found["claude-sonnet-5-5"]["name"], found["claude-sonnet-5-5"]["description"]), ("Claude Sonnet 5.5", "Efficient for routine tasks"))
        self.assertEqual(found["claude-opus-5-5"]["name"], "Claude Opus 5.5")
        self.assertEqual(found["claude-sonnet-5-5"]["reasoning_options"],
                         {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high", "xhigh", "max"], "default_effort": "medium"})
        self.assertEqual(found["claude-sonnet-4-6"]["reasoning_options"]["efforts"], ["low", "medium", "high", "max"])
        self.assertEqual(found["claude-haiku-4-5-20251001"]["reasoning_options"]["efforts"], [])
        self.assertTrue(all(m["vision"] for m in found.values()))
        # A connection saved from a listed model keeps its facts: Haiku sends no effort, Sonnet 4.6 offers no xhigh.
        haiku = subscription(model="claude-haiku-4-5-20251001", reasoning_options=found["claude-haiku-4-5-20251001"]["reasoning_options"])
        self.assertEqual(config.reasoning_choices(haiku), ["on"])
        self.assertIsNone(agent_sdk.AgentSdkEndpoint(haiku)._effort())
        older = subscription(model="claude-sonnet-4-6", reasoning_options=found["claude-sonnet-4-6"]["reasoning_options"])
        self.assertEqual(config.reasoning_choices(older), ["low", "medium", "high", "max"])
        # Asked with a grading call's isolation, in a scratch folder, without a key, a model or a prompt; and stopped afterwards.
        [run] = self.listing(self.claude_control)
        for flag in ("--strict-mcp-config", "--safe-mode", "--no-session-persistence", "--disable-slash-commands"):
            self.assertIn(flag, run["args"])
        self.assertEqual(run["args"][run["args"].index("--tools") + 1], "")
        self.assertEqual(run["args"][run["args"].index("--setting-sources") + 1], "")
        self.assertNotIn("--model", run["args"])
        self.assertFalse(run["api_key"])
        self.assertIn("faculty-models-", run["tmpdir"])
        self.assertFalse(Path(run["tmpdir"]).exists())
        self.assertTrue(self.stopped(run["pid"]))

    def test_codex_lists_every_page_without_hidden_models_or_ultra(self):
        from grading.model_catalog import subscription_listing
        from grading.providers import codex_sdk
        with patch.dict("os.environ", {"OPENAI_API_KEY": "should-never-pass"}):
            listed = subscription_listing("chatgpt")
        self.assertFalse(listed["stale"])
        found = {m["id"]: m for m in listed["models"]}
        self.assertEqual(list(found), ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-6-text"])  # three pages; the hidden one left out
        self.assertEqual(found["gpt-6.1-sol"]["name"], "GPT-6.1-Sol")
        self.assertEqual(found["gpt-6.1-sol"]["reasoning_options"],
                         {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high", "xhigh", "max"], "default_effort": "low"})
        # "ultra" is never offered, not even as a model's default (the middle level stands in); a text-only model is listed as one.
        self.assertEqual(found["gpt-6-text"]["reasoning_options"], {"supported": True, "mandatory": True, "efforts": ["low", "high"], "default_effort": "high"})
        self.assertFalse(found["gpt-6-text"]["vision"])
        self.assertTrue(found["gpt-6-luna"]["vision"])
        endpoint = config.endpoint_entry({"name": "chatgpt", "kind": "codex_sdk", "model": "gpt-6.1-sol", "reasoning_options": found["gpt-6.1-sol"]["reasoning_options"]}, saving=True)
        self.assertEqual(config.reasoning_choices(endpoint), ["low", "medium", "high", "xhigh", "max"])
        [run] = self.listing(self.codex_control)
        self.assertEqual(run["args"][:3], ["app-server", "--listen", "stdio://"])
        self.assertIn("mcp_servers={}", run["args"])
        self.assertIn('web_search="disabled"', run["args"])
        self.assertEqual([run["args"][i + 1] for i, a in enumerate(run["args"]) if a == "--disable"], list(codex_sdk.DISABLED))
        self.assertFalse(run["api_key"])
        self.assertTrue(self.stopped(run["pid"]))

    def test_a_program_that_cannot_say_leaves_the_built_in_list_and_the_reason(self):
        from grading.model_catalog import subscription_listing
        from grading.providers import codex_sdk
        self.claude_control.write_text(json.dumps({"script": "", "listing": "exit"}))
        listed = subscription_listing("claude")
        self.assertTrue(listed["stale"])
        self.assertEqual([m["id"] for m in listed["models"]], [m for m, _ in agent_sdk.MODELS])
        self.assertEqual(listed["notice"], "Claude Code stopped before naming its models (exit 3). Showing Faculty's built-in list of your Claude plan's models.")
        # One that never answers is stopped after LIST_SECONDS.
        self.claude_control.write_text(json.dumps({"script": "", "listing": "silent"}))
        with patch.object(agent_sdk, "LIST_SECONDS", 1):
            started = time.monotonic()
            listed = subscription_listing("claude")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("did not name its models within 1 s", listed["notice"])
        self.assertTrue(self.stopped(self.listing(self.claude_control)[-1]["pid"]))
        # Codex refusing the list, or a program not installed or signed in.
        self.codex_control.write_text(json.dumps({"script": "", "listing": "error"}))
        listed = subscription_listing("chatgpt")
        self.assertIn("Codex did not list its models: not signed in. Showing", listed["notice"])
        self.assertEqual([(m["id"], m["reasoning_options"]["efforts"]) for m in listed["models"]], [(m[0], list(m[2])) for m in codex_sdk.MODELS])
        with patch.object(agent_sdk, "cli", lambda: ""):
            self.assertIn("The claude command is not installed", subscription_listing("claude")["notice"])
        with patch.object(codex_sdk, "signed_in", lambda: False):
            self.assertIn("Codex is not signed in", subscription_listing("chatgpt")["notice"])
        # Nothing usable named: the built-in list, said so.
        self.claude_control.write_text(json.dumps({"script": "", "models": [{"value": "gpt-oops", "displayName": "Not Claude"}]}))
        listed = subscription_listing("claude")
        self.assertTrue(listed["stale"])
        self.assertIn("named no models Faculty can use", listed["notice"])
