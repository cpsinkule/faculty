"""Hosted answers arrive as a stream and are reassembled into the ordinary completion (2026-09-23).

Vercel's AI Gateway ends a NON-streamed request at about 340 seconds while the model is still writing. A stream keeps the connection
busy; everything that reads the answer reads the same completion as before. Loopback hosts only."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from grading import config
from grading.providers.base import ProviderError, assemble_stream, request_stream
from grading.providers.cloud import HostedEndpoint
from fake_openai import FakeHost, grade_answer


def lines(*chunks, done=True, comments=True):
    out = [b": OPENROUTER PROCESSING\n", b"\n"] if comments else []
    for chunk in chunks:
        out += [b"data: " + json.dumps(chunk).encode() + b"\n", b"\n"]
    return out + ([b"data: [DONE]\n", b"\n"] if done else [])


def endpoint(host, **extra):
    return {**config.ENDPOINT, "name": "alpha", "base_url": host.url, "model": "fixture/alpha", "lineage": "alpha", "key_env": "", **extra}


class Assembly(unittest.TestCase):
    def test_chunks_become_one_completion_with_reasoning_usage_and_the_host(self):
        base = {"id": "gen-1", "model": "xiaomi/mimo-v2.6-pro", "provider": "Xiaomi"}
        raw = assemble_stream(lines(
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "", "reasoning": "Compare the "}}]},
            {**base, "choices": [{"index": 0, "delta": {"reasoning": "two readings.", "reasoning_details": [{"type": "reasoning.text", "text": "a"}]}}]},
            {**base, "choices": [{"index": 0, "delta": {"content": '{"decision": '}}]},
            {**base, "choices": [{"index": 0, "delta": {"content": '"adopt"}', "reasoning_details": [{"type": "reasoning.text", "text": "b"}]}}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {**base, "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0012}}))
        message = raw["choices"][0]["message"]
        self.assertEqual((message["role"], message["content"], message["reasoning"]), ("assistant", '{"decision": "adopt"}', "Compare the two readings."))
        self.assertEqual([d["text"] for d in message["reasoning_details"]], ["a", "b"])
        self.assertEqual((raw["choices"][0]["finish_reason"], raw["usage"]["cost"], raw["provider"], raw["id"], raw["streamed"]), ("stop", 0.0012, "Xiaomi", "gen-1", True))

    def test_a_stream_cut_before_its_finish_has_an_unknown_outcome(self):
        with self.assertRaises(ProviderError) as caught:
            assemble_stream(lines({"choices": [{"index": 0, "delta": {"content": '{"deci'}}]}, done=False))
        self.assertIs(caught.exception.confirmed, False)
        self.assertIn("unknown", str(caught.exception))

    def test_an_error_inside_the_stream_is_the_host_answering_with_an_error(self):
        with self.assertRaises(ProviderError) as caught:
            assemble_stream(lines({"choices": [{"index": 0, "delta": {"content": "{"}}]},
                                  {"error": {"code": 502, "message": "upstream failed"}, "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}]}))
        self.assertEqual((caught.exception.confirmed, caught.exception.status, caught.exception.confirmation["status"]), (True, 502, 502))
        with self.assertRaises(ProviderError) as caught:
            assemble_stream(lines({"error": {"code": 504, "message": "gateway timeout"}}))
        self.assertIs(caught.exception.confirmed, False)  # A gateway's timeout says nothing about whether the model ran.

    def test_a_finished_answer_without_its_usage_chunk_is_kept_and_says_so(self):
        raw = assemble_stream(lines({"choices": [{"index": 0, "delta": {"content": "{}"}, "finish_reason": "stop"}]}, done=False))
        self.assertEqual((raw["choices"][0]["message"]["content"], raw["usage"]), ("{}", {}))
        self.assertIn("usage", raw["stream_note"])

    def test_output_beyond_the_limit_is_refused(self):
        with self.assertRaisesRegex(ProviderError, "exceeds the limit"):
            assemble_stream(lines({"choices": [{"index": 0, "delta": {"content": "x" * 5000}}]}), limit=1000)
        with self.assertRaisesRegex(ProviderError, "exceeds the limit"):  # Many small pieces add up to the same answer.
            assemble_stream(lines(*({"choices": [{"index": 0, "delta": {"reasoning": "x" * 50}}]} for _ in range(30))), limit=1000)

    def test_sparse_tool_indices_do_not_allocate_missing_calls(self):
        result = assemble_stream(lines({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 1000000000, "id": "second", "function": {"name": "second", "arguments": "{}"}},
            {"index": 0, "id": "first", "function": {"name": "first", "arguments": "{"}}]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
                          "finish_reason": "tool_calls"}]}))
        calls = result["choices"][0]["message"]["tool_calls"]
        self.assertEqual([(call["id"], call["function"]["arguments"]) for call in calls], [("first", "{}"), ("second", "{}")])

    def test_invalid_tool_indices_are_confirmed_provider_failures(self):
        for index in (-1, True, 0.5, "0"):
            with self.subTest(index=index), self.assertRaises(ProviderError) as caught:
                assemble_stream(lines({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": index}]},
                                                    "finish_reason": "tool_calls"}]}))
            self.assertIs(caught.exception.confirmed, True)

    def test_the_limit_counts_the_answer_not_the_envelope_repeated_with_every_chunk(self):
        # OpenRouter's MiMo stream (2026-09-23): the envelope and reasoning details came with every few tokens, so a whole ruling of
        # about 70,000 tokens filled 8 MB of stream and was refused after twenty minutes of thinking.
        envelope = {"id": "gen-1", "provider": "Xiaomi", "model": "xiaomi/mimo-v2.6-pro", "object": "chat.completion.chunk", "created": 1}
        chunks = [{**envelope, "choices": [{"index": 0, "delta": {"reasoning": "ab"}, "finish_reason": None, "native_finish_reason": None, "logprobs": None}]}
                  for _ in range(100)]
        chunks.append({**envelope, "choices": [{"index": 0, "delta": {"content": "{}"}, "finish_reason": "stop"}]})
        stream = lines(*chunks)
        self.assertGreater(sum(map(len, stream)), 20000)
        raw = assemble_stream(stream, limit=1000)
        self.assertEqual((raw["choices"][0]["message"]["reasoning"], raw["choices"][0]["message"]["content"]), ("ab" * 100, "{}"))


class Transport(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)

    def ask(self, **extra):
        self.host.script = lambda body, host: grade_answer({"transcript": {"lines": []}, "rubric": {"items": [{"id": "R1"}]}, "answer_parts": [""]})
        provider = HostedEndpoint(endpoint(self.host, **extra), None)
        request = {"prompt": "grade", "payload": {"rubric": {"items": [{"id": "R1"}]}}, "images": []}
        return provider, request, provider.submit(request)

    def test_a_hosted_request_asks_for_a_stream_and_reads_the_same_answer(self):
        provider, request, outcome = self.ask()
        body = self.host.requests[-1]["body"]
        self.assertEqual((body["stream"], body["stream_options"]), (True, {"include_usage": True}))
        result = outcome["result"]
        self.assertEqual(outcome["state"], "completed")
        self.assertEqual(result["output"]["applications"][0]["rubric_item_id"], "R1")
        self.assertTrue(result["raw"]["streamed"])
        self.assertGreater(result["metadata"]["usage"]["completion_tokens"], 0)
        # The request's own identity is unchanged: the stream is how it travels, not what was asked.
        logical, _ = provider.body(request)
        self.assertIs(logical["stream"], False)  # The logical request, whose hash the receipts keep; only the transport streams it.
        self.assertEqual(result["metadata"]["request_sha256"], provider.body(request)[1]["request_sha256"])

    def test_an_aggregator_router_is_not_sent_the_usage_option(self):
        self.ask(aggregator=True)
        body = self.host.requests[-1]["body"]
        self.assertTrue(body["stream"])
        self.assertNotIn("stream_options", body)

    def test_a_cut_stream_and_an_error_chunk_reach_the_retry_policy_as_unknown_and_confirmed(self):
        provider = HostedEndpoint(endpoint(self.host), None)
        request = {"prompt": "reconcile", "payload": {}, "images": []}
        for outcome, confirmed in ((("cut",), False), (("stream_error", 502), True)):
            self.host.script = lambda body, host, outcome=outcome: outcome
            with self.assertRaises(ProviderError) as caught:
                provider.submit(request)
            self.assertIs(caught.exception.confirmed, confirmed)

    def test_a_host_that_ignores_the_stream_request_is_read_as_one_document(self):
        answer = {"id": "x", "model": "m", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}], "usage": {}}

        class Plain(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                content = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Plain)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.assertEqual(request_stream(f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions", body={"model": "m"}), answer)

    def test_a_dropped_connection_names_what_happened(self):
        self.host.script = lambda body, host: ("drop",)
        with self.assertRaises(ProviderError) as caught:
            HostedEndpoint(endpoint(self.host), None).submit({"prompt": "reconcile", "payload": {}, "images": []})
        self.assertIs(caught.exception.confirmed, False)
        self.assertRegex(str(caught.exception), r"timed out \(\w+\); the outcome of this request is unknown")


if __name__ == "__main__":
    unittest.main()
