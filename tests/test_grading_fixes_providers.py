"""Regression tests for the provider fixes of the 2026-09-22 grading review.

Unknown cost for unpriced connections, gateway timeouts as unknown outcomes, Google's empty-enum refusal, keys bound to their
own hosts, and polls that report a job the host no longer holds. No credentials and no network: loopback servers only.
"""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from grading import config
from grading.providers import cloud as providers
from grading.providers.base import ProviderError, request_json
from rosters import connect
import test_grading_cloud as cloud_fixture
from fake_openai import FakeHost, default_answer

USAGE = {"prompt_tokens": 1000, "completion_tokens": 500}


def connection(base_url="https://host.example/v1", **extra):
    return {**config.ENDPOINT, "name": "manual", "base_url": base_url, "model": "lab/model", "lineage": "lab", **extra}


class RecordingHost:
    """Answers EVERY request with `answer(method, path) -> (status, body)` and records what reached it, key included."""
    def __init__(self, answer=lambda method, path: (200, {"data": []})):
        self.answer, self.requests = answer, []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                host.requests.append({"method": self.command, "path": self.path, "authorization": self.headers.get("Authorization")})
                status, body = host.answer(self.command, self.path)
                content = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            do_GET = do_POST = respond

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


class CostTests(unittest.TestCase):
    def test_a_connection_without_prices_has_an_unknown_cost_never_a_zero_one(self):
        """A zero would make a batch on the built-in Nebius endpoint, or on a manual connection left at its default prices, look
        free in its estimate and its recorded costs."""
        unknown = (None, "unavailable: no configured prices")
        self.assertEqual(providers.HostedEndpoint(connection()).cost(USAGE), unknown)
        self.assertEqual(providers.provider(config.nebius_endpoint(config.validate({}))).cost(USAGE), unknown)
        self.assertEqual(providers.DedicatedGpuProvider(connection(kind="dedicated")).cost(USAGE), unknown)
        # The host's own figure still wins, a reported zero included, and one configured price is enough to estimate.
        self.assertEqual(providers.HostedEndpoint(connection()).cost({**USAGE, "cost": .002}), (.002, "reported by the host"))
        self.assertEqual(providers.HostedEndpoint(connection()).cost({**USAGE, "cost": 0}), (0.0, "reported by the host"))
        self.assertEqual(providers.HostedEndpoint(connection(price_in=1.0)).cost(USAGE), (.001, "estimated from token usage and configured prices"))
        self.assertEqual(providers.HostedEndpoint(connection(price_in=1.0)).cost({}), (None, "unavailable"))


class TransportTests(unittest.TestCase):
    def test_a_gateway_timeout_is_an_unknown_outcome_and_an_error_answer_stays_confirmed(self):
        """Behind a gateway, 408/504/522/524 say only that something stopped waiting: the model may have run and billed it."""
        for code, confirmed, fixable in ((408, False, True), (504, False, True), (522, False, True), (524, False, True),
                                         (500, True, True), (502, True, True), (401, True, False)):
            with self.subTest(code=code), FakeHost(lambda body, host, code=code: ("status", code)) as host:
                with self.assertRaises(ProviderError) as raised:
                    request_json(host.url + "/chat/completions", body={"model": "m", "messages": []}, timeout=5)
                self.assertEqual((raised.exception.confirmed, raised.exception.status, raised.exception.fixable), (confirmed, code, fixable))
                if not confirmed:
                    self.assertIn("unknown", str(raised.exception))


class GatewayTimeoutPolicy(cloud_fixture.CloudCase):
    def test_a_gateway_timeout_is_resent_at_most_twice_like_any_unknown_outcome(self):
        self.host.script = lambda body, host: ("status", 504)
        self.configure(max_retries=5)  # A confirmed failure would be sent five times; an unknown outcome only three.
        job = self.queue()
        self.run_all()
        task = self.frq.job(job["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertIn("unknown after 2 automatic resends", task["error"])
        self.assertEqual([c["state"] for c in self.calls()], ["unknown"] * 3)
        self.assertEqual(len(self.host.requests), 3)


class SchemaRefusalTests(unittest.TestCase):
    """Google's error body says INVALID_ARGUMENT for an empty enum member too, and every grading contract has array bounds."""
    REQUEST = {"prompt": "arbitrate", "images": [], "durable": False,
               "payload": {"rubric": {"items": [{"id": "R1"}]}, "assessments": {"standard.primary": {}}, "flags": [], "standing": "standard.primary",
                           "answer_parts": [""], "transcript": {"lines": [{"id": "l1", "elements": [{"text": "x = 6"}]}]}}}

    @staticmethod
    def google(message):
        raw = json.dumps({"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}})
        return ("status", 400, {}, {"error": {"message": "Provider returned error", "code": 400, "metadata": {"provider_name": "Google", "raw": raw}}})

    EMPTY = "* GenerateContentRequest.generation_config.response_schema.properties[adopt].enum[0]: cannot be empty\n"

    def setUp(self):
        for memo in (providers.HostedEndpoint.empty_enum_refused, providers.HostedEndpoint.bounds_refused):
            memo.clear()
            self.addCleanup(memo.clear)

    @staticmethod
    def schema_of(body):
        return json.dumps(((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {})

    def test_googles_empty_enum_refusal_is_recognised_although_it_also_says_invalid_argument(self):
        refusal = self.google(self.EMPTY)
        with FakeHost(lambda body, host: refusal if '""' in self.schema_of(body) else default_answer(body)) as host:
            result = providers.HostedEndpoint(cloud_fixture.endpoint(host, "alpha-vision")).submit(self.REQUEST)
            sent = [self.schema_of(r["body"]) for r in host.requests]
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(sent), 2)                           # Refused once, sent ONCE more: never 3 attempts × 2 sends.
        self.assertIn('""', sent[0])
        self.assertNotIn('""', sent[1])
        self.assertIn('"minItems"', sent[1])                     # The bounds were not what was refused, so they stay.
        self.assertEqual((len(providers.HostedEndpoint.empty_enum_refused), providers.HostedEndpoint.bounds_refused), (1, {}))
        self.assertIn("empty enum members withheld", result["result"]["metadata"]["enum_note"])
        self.assertNotIn("schema_note", result["result"]["metadata"])

    def test_a_host_that_refuses_the_empty_member_and_then_the_bounds_gets_both_corrections(self):
        empty, bounds = self.google(self.EMPTY), self.google("Request contains an invalid argument.")
        answer = lambda body, host: empty if '""' in self.schema_of(body) else bounds if '"minItems"' in self.schema_of(body) else default_answer(body)
        with FakeHost(answer) as host:
            result = providers.HostedEndpoint(cloud_fixture.endpoint(host, "alpha-vision")).submit(self.REQUEST)
            self.assertEqual(len(host.requests), 3)
        self.assertEqual(result["state"], "completed")
        self.assertEqual((len(providers.HostedEndpoint.empty_enum_refused), len(providers.HostedEndpoint.bounds_refused)), (1, 1))
        self.assertTrue(result["result"]["metadata"]["enum_note"] and result["result"]["metadata"]["schema_note"])

    def test_a_refusal_neither_correction_explains_records_the_first_and_keeps_no_lesson(self):
        first, other = self.google(self.EMPTY), ("status", 400, {}, {"error": {"message": "Input text data may contain inappropriate content.", "code": 400}})
        with FakeHost(lambda body, host: first if '""' in self.schema_of(body) else other) as host:
            with self.assertRaises(ProviderError) as refused:
                providers.HostedEndpoint(cloud_fixture.endpoint(host, "alpha-vision")).submit(self.REQUEST)
            self.assertEqual(len(host.requests), 2)
        self.assertIn("cannot be empty", refused.exception.confirmation["body"])
        self.assertEqual((providers.HostedEndpoint.empty_enum_refused, providers.HostedEndpoint.bounds_refused), ({}, {}))


class PollTests(unittest.TestCase):
    """What a poll says must let the dispatcher settle a job instead of asking about it for ever."""
    def assert_gone(self, poll, status):
        with self.assertRaises(ProviderError) as raised:
            poll()
        error = raised.exception
        self.assertEqual((error.confirmed, error.fixable, error.status), (True, False, status))
        return error

    def test_a_job_the_host_no_longer_holds_or_a_refused_key_is_a_confirmed_failure_no_poll_can_fix(self):
        with FakeHost() as host:  # It answers 404 about a batch or a job it does not hold.
            hosted = providers.HostedEndpoint(cloud_fixture.endpoint(host, "alpha-vision", batch_api=True))
            dedicated = providers.DedicatedGpuProvider(cloud_fixture.endpoint(host, "gpu-reader", kind="dedicated"))
            self.assertTrue(self.assert_gone(lambda: hosted.poll("batch-purged"), 404).confirmation["job_gone"])
            self.assertTrue(self.assert_gone(lambda: dedicated.poll("job-purged"), 404).confirmation["job_gone"])
        # A completed batch whose output file was purged cannot be collected either.
        answer = lambda method, path: (200, {"id": "b", "status": "completed", "output_file_id": "f"}) if "/batches/" in path else (404, {"error": "gone"})
        with RecordingHost(answer) as host:
            self.assert_gone(lambda: providers.HostedEndpoint(connection(host.url, batch_api=True)).poll("b"), 404)
        for status in (401, 403):
            with self.subTest(status=status), RecordingHost(lambda method, path, status=status: (status, {"error": "refused"})) as host:
                self.assert_gone(lambda: providers.HostedEndpoint(connection(host.url, batch_api=True)).poll("b"), status)
                self.assert_gone(lambda: providers.DedicatedGpuProvider(connection(host.url, kind="dedicated")).poll("j"), status)

    def test_an_outage_while_asking_stays_something_to_ask_about_again(self):
        for status, confirmed in ((502, True), (504, False)):
            with self.subTest(status=status), RecordingHost(lambda method, path, status=status: (status, {"error": "busy"})) as host:
                with self.assertRaises(ProviderError) as raised:
                    providers.HostedEndpoint(connection(host.url, batch_api=True)).poll("b")
                self.assertEqual((raised.exception.confirmed, raised.exception.fixable), (confirmed, True))

    def test_a_cancelled_job_is_reported_as_cancelled_and_one_being_cancelled_is_waited_on(self):
        with FakeHost() as host:
            hosted = providers.HostedEndpoint(cloud_fixture.endpoint(host, "alpha-vision", batch_api=True))
            for status, state in (("cancelling", "submitted"), ("cancelled", "cancelled"), ("expired", "expired"), ("failed", "failed")):
                with self.subTest(status=status):
                    host.batches["b"] = {"id": "b", "status": status, "polls": 0, "output": None}
                    self.assertEqual(hosted.poll("b")["state"], state)
            dedicated = providers.DedicatedGpuProvider(cloud_fixture.endpoint(host, "gpu-reader", kind="dedicated"))
            for status, state in (("CANCELLED", "cancelled"), ("FAILED", "failed"), ("TIMED_OUT", "failed"), ("IN_QUEUE", "submitted")):
                with self.subTest(status=status):
                    host.jobs["j"] = {"id": "j", "status": status}
                    self.assertEqual(dedicated.poll("j")["state"], state)


class KeyBindingTests(unittest.TestCase):
    ROUTER = {"key_env": "OPENROUTER_API_KEY", "base_url": config.AGGREGATOR}

    def settings(self, **change):
        return {"endpoints": [connection(**{**self.ROUTER, **change})], "yardstick": "",
                "tiers": {"cloud_standard": {"reader": "manual", "primary": "manual", "second": "", "verifier": ""}}}

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("NEBIUS_GRADING_URL", None)  # The server's own Nebius URL is set per test where it matters.

    def test_saving_refuses_a_key_bound_to_another_host_and_allows_its_own(self):
        for change in ({"base_url": "https://collector.example/v1"}, {"key_env": "AI_GATEWAY_API_KEY"},
                       {"key_env": "NEBIUS_API_KEY"}, {"key_env": "TYPESAFE_API_KEY"},
                       {"key_env": "TYPESAFE_API_KEY", "base_url": "https://api.typesafe.ai/v1"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY was the removed Jev|belongs to"):
                config.validate(self.settings(**change), saving=True)
        for change in ({}, {"key_env": "AI_GATEWAY_API_KEY", "base_url": config.VERCEL_GATEWAY}, {"key_env": "NEBIUS_API_KEY", "base_url": config.NEBIUS_URL},
                       {"key_env": "MY_OWN_HOST_API_KEY", "base_url": "https://collector.example/v1"}):
            with self.subTest(change=change):
                config.validate(self.settings(**change), saving=True)
        # The built-in Nebius endpoint's URL is server configuration, so a connection may use its key at that host too.
        managed = self.settings(key_env="NEBIUS_API_KEY", base_url="https://nebius.invalid/v1")
        with self.assertRaisesRegex(ValueError, "belongs to"):
            config.validate(managed, saving=True)
        with patch.dict(os.environ, {"NEBIUS_GRADING_URL": "https://nebius.invalid/v1"}):
            config.validate(managed, saving=True)
            self.assertEqual(config.key_problem(config.nebius_endpoint(config.validate({}))), "")
        self.assertEqual(config.key_problem(config.nebius_endpoint(config.validate({}))), "")
        self.assertTrue(all(config.key_problem(e) == "" for e in config.DEFAULTS["endpoints"]))  # Every shipped connection keeps its key.

    def test_a_stored_connection_that_breaks_the_binding_still_loads_exactly_as_stored(self):
        """Settings ALWAYS load: the page and every frozen batch keep working; only the key is never sent."""
        for change in ({"base_url": "https://collector.example/v1"}, {"key_env": "TYPESAFE_API_KEY"}):
            with self.subTest(change=change):
                clean = config.validate(self.settings(**change))
                stored = clean["endpoints"][0]
                self.assertEqual((stored["key_env"], stored["base_url"]), (change.get("key_env", "OPENROUTER_API_KEY"), change.get("base_url", config.AGGREGATOR)))
                self.assertTrue(config.key_problem(stored))
                self.assertEqual(config.roles(clean, "cloud_standard")["primary"]["name"], "manual")

    def test_a_bound_key_never_leaves_for_a_host_it_does_not_belong_to(self):
        with RecordingHost() as host, patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-bound-secret", "MY_OWN_HOST_API_KEY": "sk-own"}):
            hosted = providers.HostedEndpoint(connection(host.url, key_env="OPENROUTER_API_KEY", batch_api=True, aggregator=True))
            dedicated = providers.DedicatedGpuProvider(connection(host.url, key_env="OPENROUTER_API_KEY", kind="dedicated"))
            self.assertFalse(hosted.configured())
            self.assertEqual(hosted.listing()["status"], "key_not_allowed_for_host")
            health = hosted.health()
            self.assertEqual(health["status"], "key_not_allowed_for_host")
            self.assertIn("OPENROUTER_API_KEY belongs to openrouter.ai", health["reason"])
            self.assertEqual(dedicated.health()["status"], "key_not_allowed_for_host")
            request = {"prompt": "probe", "payload": {"task": "Add 17 and 25."}, "images": [], "schema": {"type": "object"}}
            for attempt in (lambda: hosted.submit(request), lambda: hosted.submit({**request, "durable": False}), lambda: hosted.poll("b"),
                            lambda: hosted.cancel("b"), lambda: dedicated.submit(request), lambda: dedicated.poll("j"), lambda: dedicated.cancel("j")):
                with self.assertRaises(ProviderError) as refused:
                    attempt()
                self.assertEqual((refused.exception.confirmed, refused.exception.fixable), (True, False))
                self.assertIn("belongs to openrouter.ai", str(refused.exception))
            self.assertEqual(host.requests, [])  # Nothing at all reached the host, least of all the key.
            # A key bound to no host is still the instructor's to point anywhere.
            providers.HostedEndpoint(connection(host.url, key_env="MY_OWN_HOST_API_KEY")).listing()
            self.assertEqual([r["authorization"] for r in host.requests], ["Bearer sk-own"])


class StoredViolatingConnectionTests(cloud_fixture.CloudCase):
    def test_a_connection_stored_before_the_binding_loads_and_its_batch_stops_before_the_key_leaves(self):
        self.configure()
        with connect(self.path) as db:  # Stored before the binding existed: OpenRouter's key named for this (loopback) host.
            saved = json.loads(db.execute("SELECT settings_json FROM frq_settings WHERE id=1").fetchone()[0])
            saved["endpoints"][0]["key_env"] = "OPENROUTER_API_KEY"
            db.execute("UPDATE frq_settings SET settings_json=? WHERE id=1", (json.dumps(saved),))
        loaded = self.frq.settings()["settings"]
        self.assertEqual(loaded["endpoints"][0]["key_env"], "OPENROUTER_API_KEY")  # The settings page still opens, unchanged.
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-bound-secret"}):
            self.assertEqual(self.frq.health(refresh=True)["endpoints"]["alpha-vision"]["status"], "key_not_allowed_for_host")
            job = self.queue()
            self.run_all()
        task = self.frq.job(job["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertIn("OPENROUTER_API_KEY belongs to openrouter.ai", task["error"])
        self.assertEqual([c["state"] for c in self.calls()], ["failed"])  # Stopped at once: the same request would fail the same way.
        self.assertEqual(self.host.requests, [])
        with connect(self.path) as db:
            dump = "\n".join(str(tuple(row)) for table in ("frq_settings", "frq_jobs", "frq_cloud_calls", "frq_events") for row in db.execute(f"SELECT * FROM {table}"))
        self.assertNotIn("sk-bound-secret", dump)


if __name__ == "__main__":
    unittest.main()
