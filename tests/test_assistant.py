"""The Faculty assistant (AGENTS.md "Faculty assistant"): every write route has a tier; the instructor's chosen categories run at
once and anything more waits for approval; the assistant never supplies a confirmation and can never reach its own approval; an
Approve card runs the page's preview again and submits the ordinary route only on the instructor's click; reads leave credentials
out; the catalog is read-only; the MCP proxy only relays; the hosted engine's tool loop runs the same tools."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from browser_course_pages import seed_course
from test_storage import HEADERS

from app import create_app
from assistant import policy
from assistant.transport import clean
from grading import config
from grading.providers.base import assemble_stream
from rosters import connect


def settle(seconds=10):
    """Wait for the Assistant's own threads to end: a conversation's turn (`assistant-<id>`, which saves the conversation after
    it stops being busy) and a typed message being considered for memory (`assistant-memory-<id>`, started by the messages route
    and held by nobody). Both read and write the catalog, so a test's folder is removed only after them."""
    until = time.monotonic() + seconds
    while True:
        running = [thread for thread in threading.enumerate() if thread.name.startswith('assistant-')]
        if not running or time.monotonic() >= until:
            break
        for thread in running:  # a turn that ends may start the next one: look again after these
            thread.join(max(0, until - time.monotonic()))
    assert not running, f'Assistant threads still running: {sorted(thread.name for thread in running)}'


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(settle)  # runs before the folder is removed
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.service = self.app.extensions["assistant"]
        self.session = self.service.open_session("test")

    def tool(self, tool_name, **args):
        text, event = self.service.run_tool(self.session, tool_name, args)
        return json.loads(text), event

    def write(self, method, path, body, summary="A test change"):
        return self.tool("faculty_write", method=method, path=path, body=body, summary=summary)[0]

    def actions(self):
        return self.client.get("/api/assistant/actions").json


class PolicyTest(Base):
    def test_every_route_named_as_needing_a_request_id_is_a_write_the_assistant_may_prepare(self):
        # ASSISTANT (2026-10-07): Faculty supplies the request ID these routes refuse a write without (REQUEST_IDS).
        writes = {r.endpoint for r in self.app.url_map.iter_rules() if set(r.methods) & {"POST", "PUT", "PATCH", "DELETE"}}
        self.assertEqual(sorted(policy.REQUEST_IDS - writes), [])
        for endpoint in policy.REQUEST_IDS:
            rule = policy.base_rule(endpoint)
            self.assertIn(rule.tier, ("approve", "auto"), endpoint)
            self.assertTrue(rule.request_id, endpoint)
        self.assertTrue(policy.base_rule("inbox.change_job", {"operation": "start-over"}).request_id)
        self.assertFalse(policy.base_rule("inbox.change_job", {"operation": "delete"}).request_id)  # it refuses unknown fields
        self.assertFalse(policy.base_rule("workspace_calendar.delete").request_id)

    def test_every_write_route_has_a_tier_and_unknown_routes_wait_for_approval(self):
        for rule in self.app.url_map.iter_rules():
            for method in set(rule.methods) - {"GET", "HEAD", "OPTIONS"}:
                found = policy.base_rule(rule.endpoint, {"operation": "retry"} if "<operation>" in rule.rule else {})
                self.assertIn(found.tier, policy.TIERS, rule.endpoint)
                if rule.endpoint.startswith("assistant."):
                    allowed = {"assistant.code_task_start": "approve", "assistant.watch_create": "approve", "assistant.artifact_create": "approve",
                               "assistant.watch_preview": "read", "assistant.artifact_preview": "read",
                               "assistant.workflow_create": "approve", "assistant.preparation_create": "auto", "assistant.preparation_continue": "auto", "assistant.workflow_preview": "read",
                               "assistant.material_preview": "read", "assistant.material_create": "approve"}
                    self.assertEqual(found.tier, allowed.get(rule.endpoint, "never"), rule.endpoint)
        self.assertEqual(policy.base_rule("some_module.new_route").tier, "approve")
        for endpoint in ("inbox.new_key", "gmail_mail.connect", "canvas_signin.signin_credentials", "accommodations.aim_credentials",
                         "frq_grading.save_settings", "inbox.upload_scan", "assistant.approve"):
            self.assertEqual(policy.base_rule(endpoint).tier, "never", endpoint)
        self.assertEqual(policy.base_rule("frq_certification.certify").tier, "page")
        # An exam-day plan (proctors and instructions) is not one of the instructor's automatic categories: it waits for approval.
        self.assertEqual(policy.base_rule("exam_day.save").tier, "approve")
        self.assertEqual(policy.base_rule("inbox.change_job", {"operation": "retry"}).tier, "auto")
        deleting = policy.base_rule("inbox.change_job", {"operation": "delete"})
        self.assertEqual((deleting.tier, deleting.confirmation), ("approve", "delete"))
        self.assertEqual(policy.base_rule("gmail_mail.send").confirmation, "send")

    def test_rubric_comments_run_at_once_but_wording_waits(self):
        context = self.service.context
        comments = policy.classify("frq_grading.edit_rubric", "POST", {"aid": "a", "qid": "q"}, {"comments": {"R1": "note"}}, context)
        wording = policy.classify("frq_grading.edit_rubric", "POST", {"aid": "a", "qid": "q"}, {"lines": [{"item_id": "R1", "description": "x"}]}, context)
        self.assertEqual(comments.tier, "auto")
        self.assertEqual(wording.tier, "approve")
        self.assertIn("wording", wording.reason)

    def test_retries_of_a_certified_assignment_wait(self):
        with patch.object(type(self.service.context), "job_assignment", return_value="a"), \
                patch.object(type(self.service.context), "ever_certified", return_value=True):
            found = policy.classify("frq_grading.retry", "POST", {"jid": "j"}, {}, self.service.context)
        self.assertEqual(found.tier, "approve")
        with patch.object(type(self.service.context), "job_assignment", return_value="a"), \
                patch.object(type(self.service.context), "ever_certified", return_value=False):
            self.assertEqual(policy.classify("frq_grading.retry", "POST", {"jid": "j"}, {}, self.service.context).tier, "auto")


class GradebookSetupTest(Base):
    def setUp(self):
        super().setUp()
        seed_course(self.client, sections=[507, 508])
        with connect(self.app.config["DATABASE"]) as db:
            self.term = db.execute("SELECT id FROM course_terms").fetchone()["id"]
        self.path = f"/api/gradebook/terms/{self.term}/setup"

    def setup_now(self):
        return self.client.get(f"/api/gradebook/terms/{self.term}").json["setup"]

    def test_adding_and_renaming_columns_is_automatic_but_categories_wait(self):
        added = self.write("POST", self.path, {"base_revision": None, "request_id": "$new_request_id",
                                               "setup": {"items": [{"title": "Quiz 1", "points": "10", "due_date": "2026-10-01"}]}})
        self.assertEqual(added["outcome"], "done", added)
        setup = self.setup_now()
        item = setup["items"][0]
        renamed = self.write("POST", self.path, {"base_revision": setup["revision"], "request_id": "$new_request_id",
                                                 "setup": {"items": [{**item, "title": "Quiz 1 (retake)", "due_date": "2026-10-03"}]}})
        self.assertEqual(renamed["outcome"], "done", renamed)
        self.assertEqual(self.setup_now()["items"][0]["title"], "Quiz 1 (retake)")
        setup = self.setup_now()
        weighted = self.write("POST", self.path, {"base_revision": setup["revision"], "request_id": "$new_request_id",
                                                  "setup": {"categories": [{"name": "Quizzes", "weight": "50"}]}})
        self.assertEqual(weighted["outcome"], "waiting_for_approval", weighted)
        points = self.write("POST", self.path, {"base_revision": setup["revision"], "request_id": "$new_request_id",
                                                "setup": {"items": [{**setup["items"][0], "points": "20"}]}})
        self.assertEqual(points["outcome"], "waiting_for_approval")
        removed = self.write("POST", self.path, {"base_revision": setup["revision"], "request_id": "$new_request_id", "setup": {"items": []}})
        self.assertEqual(removed["outcome"], "waiting_for_approval")
        self.assertIn("removes a column", removed["why_approval"])


class WriteFlowTest(Base):
    def test_automatic_changes_are_made_and_logged(self):
        result = self.write("POST", "/api/calendar/events", {"request_id": "$new_request_id", "title": "Office hours", "date": "2026-10-01", "all_day": True})
        self.assertEqual((result["outcome"], result["status"]), ("done", 201))
        self.assertEqual(result["result"]["title"], "Office hours")
        logged = self.actions()["recent"][0]
        self.assertEqual((logged["tier"], logged["status"], logged["pending"]), ("auto", "succeeded", False))
        self.assertNotEqual(logged["body"]["request_id"], "$new_request_id")

    def test_a_request_id_the_model_left_out_or_made_up_is_supplied(self):
        # ASSISTANT (2026-10-07): a card without one failed only at the instructor's Approve; the calendar takes only a UUID.
        for given in (None, "office-hours-1"):
            result = self.write("POST", "/api/calendar/events", {**({"request_id": given} if given else {}), "title": f"Office hours {given}",
                                                                  "date": "2026-10-01", "all_day": True})
            self.assertEqual((result["outcome"], result["status"]), ("done", 201), result)
            sent = self.actions()["recent"][0]["body"]["request_id"]
            self.assertEqual(str(uuid.UUID(sent)), sent)
        kept = str(uuid.uuid4())  # one Faculty made (an earlier card's, retried) is kept
        self.write("POST", "/api/calendar/events", {"request_id": kept, "title": "Office hours again", "date": "2026-10-02", "all_day": True})
        self.assertEqual(self.actions()["recent"][0]["body"]["request_id"], kept)

    def test_an_appointment_card_drops_the_conversation_key(self):
        # APPOINTMENTS ARE NOT CONVERSATIONS (2026-10-16): with the email's source_key the card failed only at Approve.
        result = self.write("POST", "/api/student-support", {"kind": "appointment", "enrollment_id": "e1", "title": "Meeting",
                                                             "date": "2026-10-20", "time": "13:00", "end_time": "13:30",
                                                             "location": "BLOC 319", "source_key": "gmail:thread-1"})
        self.assertIn("`source_key` was removed", result["note"])
        self.assertEqual(result["outcome"], "waiting_for_approval")
        self.assertNotIn("source_key", self.actions()["pending"][0]["body"])

    def test_an_approve_card_is_made_only_by_the_instructors_click(self):
        result = self.write("POST", "/api/tools", {"name": "Canvas", "url": "https://canvas.tamu.edu", "confirmation": "yes"})
        self.assertEqual(result["outcome"], "waiting_for_approval")
        self.assertIn("confirmation field was removed", result["note"])
        self.assertNotIn("Canvas", [t["name"] for t in self.client.get("/api/state").json["tools"]])
        card = self.actions()["pending"][0]
        self.assertNotIn("confirmation", card["body"])
        stale = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"] + 1}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        done = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(done.status_code, 200, done.json)
        self.assertEqual(done.json["action"]["status"], "succeeded")
        self.assertIn("Canvas", [t["name"] for t in self.client.get("/api/state").json["tools"]])
        again = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(again.status_code, 409)
        self.assertEqual([t["name"] for t in self.client.get("/api/state").json["tools"]].count("Canvas"), 1)

    def test_the_restart_check_waits_for_an_approval_being_carried_out(self):
        """AGENTS.md CHECKS (2026-10-28): a restart while an Approve ran cut a regrade decision's approval before anything was
        recorded; the check names it until Faculty's answer is recorded."""
        self.write("POST", "/api/tools", {"name": "Canvas", "url": "https://canvas.tamu.edu"})
        card = self.actions()["pending"][0]
        seen, request = [], self.service.transport.request
        def during(*args):
            seen.append(self.client.get("/api/assistant/restart-check").json)
            return request(*args)
        with patch.object(self.service.transport, "request", side_effect=during):
            done = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(done.json["action"]["status"], "succeeded")
        self.assertEqual(seen, [{"idle": False, "busy": {"approvals": 1}, "canvas_writes": []}])
        self.assertEqual(self.client.get("/api/assistant/restart-check").json, {"idle": True, "busy": {}, "canvas_writes": []})

    def test_declined_and_expired_cards_are_never_submitted(self):
        self.write("POST", "/api/tools", {"name": "One", "url": "https://one.example"})
        card = self.actions()["pending"][0]
        self.assertEqual(self.client.post(f"/api/assistant/actions/{card['id']}/decline", json={}, headers=HEADERS).json["status"], "declined")
        self.assertEqual(self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS).status_code, 409)
        self.write("POST", "/api/tools", {"name": "Two", "url": "https://two.example"})
        card = self.actions()["pending"][0]
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("DROP TRIGGER assistant_actions_immutable")
            db.execute("UPDATE assistant_actions SET created_at=? WHERE id=?", (old, card["id"]))
        self.assertEqual(self.actions()["pending"], [])
        names = [t["name"] for t in self.client.get("/api/state").json["tools"]]
        self.assertNotIn("One", names)
        self.assertNotIn("Two", names)

    def test_a_refused_preview_makes_no_card(self):
        result = self.write("POST", "/api/mail/send", {"to": ["a@tamu.edu"], "subject": "Hi", "body": "Hello", "request_id": "$new_request_id"})
        self.assertEqual(result["outcome"], "refused_by_preview")
        self.assertIn("Connect Gmail", result["error"])
        self.assertEqual(self.actions()["recent"], [])

    def test_a_preview_that_changed_refreshes_the_card_instead_of_submitting(self):
        rule = policy.Rule("approve", preview="gmail_mail.preview", confirmation="send")
        previews = iter([{"status": 200, "data": {"subject": "Hi"}, "fingerprint": "first", "error": None},
                         {"status": 200, "data": {"subject": "Hi!"}, "fingerprint": "second", "error": None}])
        with patch.dict(policy.APPROVE, {"gmail_mail.send": rule}), patch.object(type(self.service), "_preview", side_effect=lambda *a: next(previews)):
            self.assertEqual(self.write("POST", "/api/mail/send", {"subject": "Hi"})["outcome"], "waiting_for_approval")
            card = self.actions()["pending"][0]
            answer = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS).json
        self.assertTrue(answer["stale"])
        self.assertEqual(answer["action"]["status"], "previewed")
        self.assertEqual(answer["action"]["preview"]["fingerprint"], "second")

    def test_routes_the_assistant_may_not_use(self):
        self.assertEqual(self.write("POST", "/api/inbox/keys", {})["outcome"], "refused")
        self.assertEqual(self.write("POST", "/api/assistant/actions/x/approve", {"seen": 1})["outcome"], "refused")
        certify = self.write("POST", "/api/grading/assignments/a/certification", {})
        self.assertEqual(certify["outcome"], "use_the_page")
        self.assertIn("error", self.tool("faculty_write", method="GET", path="/api/state", summary="x")[0])
        self.assertIn("error", self.tool("faculty_write", method="POST", path="/api/tools", body={})[0])  # no summary


class ReadTest(Base):
    def test_reads_leave_credentials_out_and_skip_blocked_routes(self):
        self.assertEqual(clean({"token": "abc", "access_token": "x", "items": [{"refresh_token": "y", "name": "n"}], "tokens_used": 3}),
                         {"token": "[removed]", "access_token": "[removed]", "items": [{"refresh_token": "[removed]", "name": "n"}], "tokens_used": 3})
        for path in ("/api/export", "/api/response-cards/cards/k/link", "/api/assistant/actions", "/api/student-photos/1234567890"):
            self.assertIn("does not read", self.tool("faculty_get", path=path)[0]["error"], path)
        overview, event = self.tool("workspace_overview")
        self.assertIn("attention", overview)
        self.assertEqual(event["label"], "Read workspace overview")
        self.assertIn("error", self.tool("faculty_get", path="https://example.com/api/state")[0])

    def test_a_hidden_grade_is_never_revealed_by_a_read(self):
        with patch.object(self.service.transport, "request", return_value=(200, {}, "")) as sent:
            self.tool("faculty_get", path="/api/grading/responses/r1?reveal=1")
        self.assertNotIn("reveal", sent.call_args.args[1])

    def test_the_catalog_is_read_only_and_bounded(self):
        rows = self.tool("catalog_sql", query="SELECT namespace, version FROM schema_migrations WHERE namespace='assistant'")[0]
        self.assertEqual(rows["rows"], [["assistant", "001_actions"], ["assistant", "002_code_tasks"], ["assistant", "003_workspace"],
                                        ["assistant", "004_preparations"], ["assistant", "005_watch_preparation"],
                                        ["assistant", "006_daily_reconciliation"], ["assistant", "007_workflow_capabilities"], ["assistant", "008_material_failed"],
                                        ["assistant", "009_preparation_status"], ["assistant", "010_memory"], ["assistant", "011_memory_pictures"],
                                        ["assistant", "012_draft_memories"], ["assistant", "013_memory_always"], ["assistant", "014_memory_settings"],
                                        ["assistant", "015_memory_said_in_a_recording"]])
        for statement in ("DELETE FROM courses", "ATTACH DATABASE ':memory:' AS x", "PRAGMA writable_schema=1", "CREATE TABLE t(x)"):
            self.assertIn("error", self.tool("catalog_sql", query=statement)[0], statement)
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("CREATE TABLE secret_test(id INTEGER, access_token TEXT, label TEXT)")
            db.execute("INSERT INTO secret_test VALUES(1,'abc','x')")
        self.assertEqual(self.tool("catalog_sql", query="SELECT * FROM secret_test")[0]["rows"], [[1, None, "x"]])
        with patch("assistant.tools.SQL_SECONDS", 0.01):
            slow = self.tool("catalog_sql", query="WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT count(*) FROM n")[0]
        self.assertIn("stopped", slow["error"])

    def test_documents_and_source(self):
        documents = self.tool("read_docs")[0]["documents"]
        # QUIET STEPS: the primer is in the instructions whole; it is not listed, a read of it is a note, and the note is no source.
        from assistant.conversations import SYSTEM
        self.assertIn("{guide}", SYSTEM)
        self.assertNotIn("assistant/guide.md", documents)
        primer, event = self.tool("read_docs", name="assistant/guide.md")
        self.assertIn("already in your instructions", primer["note"])
        self.assertNotIn("text", primer)
        self.assertNotIn("evidence", event)
        self.assertTrue(self.tool("read_docs", name="AGENTS.md")[1]["evidence"])
        self.assertFalse([name for name in documents if name.startswith(".")])
        self.assertIn("## TA contacts", self.tool("read_docs", name="AGENTS.md", section="TA contacts")[0]["text"])
        self.assertIn("error", self.tool("read_source", path="../.config/faculty/gradescope.json")[0])
        self.assertTrue(self.tool("search_source", pattern="def save_setup", path_prefix="gradebook/")[0]["hits"])
        routes = self.tool("list_actions", filter="teaching-assistants")[0]["routes"]
        self.assertEqual({(r["method"], r["assistant"]) for r in routes}, {("GET", "read"), ("POST", "auto")})


class McpProxyTest(unittest.TestCase):
    def test_malformed_frames_do_not_end_the_proxy(self):
        import manage_assistant

        frames = [[], None, 7, "text", {"id": 1, "method": "initialize", "params": [1]},
                  {"id": 2, "method": "ping"}]
        out = io.StringIO()
        manage_assistant.serve(object(), io.StringIO("\n".join(map(json.dumps, frames))), out)
        answers = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([answer["error"]["code"] for answer in answers[:-1]], [-32600] * 4 + [-32602])
        self.assertEqual(answers[-1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_the_proxy_relays_lists_and_calls(self):
        import manage_assistant

        class Faculty:
            def tools(self):
                return [{"name": "courses", "description": "d", "inputSchema": {"type": "object"}}]

            def run(self, name, arguments):
                return json.dumps({"name": name, "arguments": arguments}), False
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "courses", "arguments": {"a": 1}}},
                 {"jsonrpc": "2.0", "id": 4, "method": "nothing"}]
        out = io.StringIO()
        manage_assistant.serve(Faculty(), io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n"), out)
        answers = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([a["id"] for a in answers], [1, 2, 3, 4])
        self.assertEqual(answers[1]["result"]["tools"][0]["name"], "courses")
        self.assertEqual(json.loads(answers[2]["result"]["content"][0]["text"]), {"name": "courses", "arguments": {"a": 1}})
        self.assertEqual(answers[3]["error"]["code"], -32601)

    def test_the_proxy_through_http_routes(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        app = create_app(os.path.join(temp.name, "data"))
        client = app.test_client()
        opened = client.post("/api/assistant/sessions", json={"engine": "external"}, headers=HEADERS).json
        called = client.post(f"/api/assistant/sessions/{opened['id']}/tools/courses", json={"arguments": {}},
                             headers={**HEADERS, "X-Assistant-Token": opened["token"]})
        self.assertEqual(called.status_code, 200)
        self.assertIn("course_prefixes", called.json["text"])
        wrong = client.post(f"/api/assistant/sessions/{opened['id']}/tools/courses", json={"arguments": {}},
                            headers={**HEADERS, "X-Assistant-Token": "nope"})
        self.assertEqual(wrong.status_code, 410)


def chunk(delta, finish=None):
    return b"data: " + json.dumps({"id": "c", "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}).encode()


class HostedEngineTest(Base):
    def test_tool_call_pieces_are_joined_by_index(self):
        lines = [chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "courses", "arguments": ""}}]}),
                 chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"a\""}}]}),
                 chunk({"tool_calls": [{"index": 1, "id": "call_2", "function": {"name": "calendar", "arguments": "{}"}}]}),
                 chunk({"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}, "tool_calls"), b"data: [DONE]"]
        calls = assemble_stream(lines)["choices"][0]["message"]["tool_calls"]
        self.assertEqual([(c["id"], c["function"]["name"], c["function"]["arguments"]) for c in calls],
                         [("call_1", "courses", "{\"a\": 1}"), ("call_2", "calendar", "{}")])

    def test_a_conversation_runs_tools_and_answers(self):
        from assistant.conversations import Conversation
        endpoint = {**config.ENDPOINT, "name": "helper", "kind": "hosted", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY",
                    "model": "some/model", "aggregator": False, "reasoning_choice": "off"}
        answers = iter([
            {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t1", "function": {"name": "courses", "arguments": "{}"}}]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}},
            {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t2", "function": {"name": "faculty_write", "arguments": json.dumps(
                {"method": "POST", "path": "/api/tools", "summary": "Add Canvas", "body": {"name": "Canvas", "url": "https://canvas.tamu.edu"}})}}]}}], "usage": {}},
            {"choices": [{"message": {"content": "You have no courses yet. The Canvas link **waits for your approval**."}}], "usage": {"cost": 0.002}}])
        sent = []

        def fake(url, *, headers, timeout, body, usage_option):
            sent.append(body)
            return next(answers)
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "test"}), patch("assistant.engines.request_stream", side_effect=fake):
            conversation = Conversation(self.service, self.session, endpoint, "http://127.0.0.1:1", "#overview")
            conversation.send("What courses do I have? Add a Canvas link.")
            for _ in range(200):
                if not conversation.busy:
                    break
                import time
                time.sleep(0.02)
        kinds = [(e["kind"], e.get("label") or e.get("text", "")[:30]) for e in conversation.events]
        self.assertEqual([k for k, _ in kinds], ["user", "tool", "tool", "assistant"], kinds)
        self.assertEqual(conversation.events[2]["action"]["outcome"], "waiting_for_approval")
        self.assertEqual(sent[0]["tools"][0]["type"], "function")
        self.assertNotIn("reasoning", sent[0])
        self.assertEqual(sent[-1]["messages"][-1]["role"], "tool")
        self.assertIn("Faculty assistant", sent[0]["messages"][0]["content"])
        self.assertEqual(conversation.tokens, {"input": 10, "output": 2})
        self.assertEqual(len(self.actions()["pending"]), 1)


class ConversationRouteTest(Base):
    def test_a_conversation_starts_answers_and_carries_what_is_on_screen(self):
        current = self.client.get("/api/grading/settings").json
        settings = current["settings"]
        settings["endpoints"].append({"name": "helper", "kind": "hosted", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY",
                                      "model": "some/model", "modality": "text", "lineage": "", "aggregator": False})
        settings["assistant_model"] = "helper"
        self.assertEqual(self.client.put("/api/grading/settings", json={"settings": settings, "version": current["version"]}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.get("/api/assistant/status").json["busy"], 0)
        started = self.client.post("/api/assistant/conversations", json={"page": "#overview"}, headers=HEADERS)
        self.assertEqual(started.status_code, 201, started.json)
        sent_bodies = []

        def answer(url, *, headers, timeout, body, usage_option):
            sent_bodies.append(body)
            return {"choices": [{"message": {"content": "Done."}}], "usage": {}}
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "test"}), patch("assistant.engines.request_stream", side_effect=answer):
            sent = self.client.post(f"/api/assistant/conversations/{started.json['id']}/messages", headers=HEADERS,
                                    json={"text": "Hi", "page": "#gradebook", "view": {"title": "Section 507", "trail": "Home Courses", "selection": "Frost"}})
            self.assertEqual(sent.status_code, 202, sent.json)
            for _ in range(100):
                read = self.client.get(f"/api/assistant/conversations/{started.json['id']}?after=0").json
                if not read["busy"]:
                    break
                import time
                time.sleep(0.02)
        self.assertEqual([e["kind"] for e in read["events"]], ["user", "assistant"])
        system, asked = sent_bodies[0]["messages"][0]["content"], sent_bodies[0]["messages"][-1]["content"]
        self.assertIn("Each message begins with where the instructor is", system)
        self.assertIn("[I am on the page #gradebook, headed “Section 507” (Home Courses).]", asked)
        self.assertIn("Frost", asked)
        self.assertTrue(asked.endswith("Hi"))
        closed = self.client.post(f"/api/assistant/conversations/{started.json['id']}/close", json={}, headers=HEADERS)
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(self.client.get(f"/api/assistant/conversations/{started.json['id']}").status_code, 410)


class ContinuityTest(Base):
    """A conversation never simply ends under the instructor: an idle one rests and wakes with its history; a forgotten one is
    continued by a new conversation seeded from what the drawer shows."""
    def setUp(self):
        super().setUp()
        current = self.client.get("/api/grading/settings").json
        settings = current["settings"]
        settings["endpoints"].append({"name": "helper", "kind": "hosted", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY",
                                      "model": "some/model", "modality": "text", "lineage": "", "aggregator": False})
        settings["assistant_model"] = "helper"
        self.assertEqual(self.client.put("/api/grading/settings", json={"settings": settings, "version": current["version"]}, headers=HEADERS).status_code, 200)
        self.sent = []

    def answer(self, url, *, headers, timeout, body, usage_option):
        self.sent.append(body)
        return {"choices": [{"message": {"content": f"Answer {len(self.sent)}"}}], "usage": {}}

    def say(self, cid, text):
        import time
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "test"}), patch("assistant.engines.request_stream", side_effect=self.answer):
            sent = self.client.post(f"/api/assistant/conversations/{cid}/messages", json={"text": text, "page": "#overview"}, headers=HEADERS)
            self.assertEqual(sent.status_code, 202, sent.json)
            for _ in range(200):
                if not self.client.get(f"/api/assistant/conversations/{cid}").json["busy"]:
                    return
                time.sleep(0.02)

    def test_an_idle_conversation_rests_and_wakes_with_its_history(self):
        from assistant import conversations
        cid = self.client.post("/api/assistant/conversations", json={"page": "#overview"}, headers=HEADERS).json["id"]
        self.say(cid, "First question")
        found = self.service.conversations.get(cid)
        found.used -= conversations.IDLE + 1
        self.service.conversations._sweep()
        self.assertIsNone(found.engine)
        self.assertEqual(self.client.get(f"/api/assistant/conversations/{cid}").status_code, 200)
        self.say(cid, "Second question")
        self.assertIsNotNone(found.engine)
        contents = [m["content"] for m in self.sent[-1]["messages"][1:]]
        self.assertIn("Answer 1", contents)
        self.assertTrue(contents[-1].endswith("Second question"))
        found.used -= conversations.KEEP + 1
        self.assertEqual(self.client.get(f"/api/assistant/conversations/{cid}").status_code, 410)

    def test_a_new_conversation_starts_from_the_history_the_drawer_shows(self):
        history = [{"role": "instructor", "text": "Who is in 507?"}, {"role": "assistant", "text": "Ann and Bo."},
                   {"role": "someone", "text": "ignored"}, {"role": "instructor", "text": ""}]
        cid = self.client.post("/api/assistant/conversations", json={"page": "#overview", "history": history}, headers=HEADERS).json["id"]
        self.say(cid, "And in 508?")
        messages = self.sent[0]["messages"]
        self.assertEqual([(m["role"], m["content"]) for m in messages[1:3]], [("user", "Who is in 507?"), ("assistant", "Ann and Bo.")])
        self.assertTrue(messages[-1]["content"].endswith("And in 508?"))
        replay = self.service.conversations.get(cid).replay_text()
        self.assertIn("Instructor: Who is in 507?", replay)


FAKE_APP_SERVER = r'''
import json, sys, pathlib
log = pathlib.Path(LOG)
log.write_text(json.dumps(sys.argv[1:]) + "\n")
def send(message):
    sys.stdout.write(json.dumps(message) + "\n"); sys.stdout.flush()
turns = 0
for line in sys.stdin:
    message = json.loads(line)
    with log.open("a") as out:
        out.write(line)
    method, ident, params = message.get("method"), message.get("id"), message.get("params") or {}
    if "method" not in message:
        continue  # the client's answer to a request of ours
    if method == "initialize":
        send({"id": ident, "result": {"userAgent": "fake"}})
    elif method == "thread/start":
        send({"id": ident, "result": {"thread": {"id": "th1"}}})
    elif method == "turn/start":
        turns += 1
        send({"id": ident, "result": {"turn": {"id": f"tu{turns}"}}})
        send({"id": 900 + turns, "method": "item/commandExecution/requestApproval", "params": {"threadId": "th1", "turnId": f"tu{turns}"}})
        if "hang" in params["input"][0]["text"]:
            continue
        send({"method": "item/completed", "params": {"threadId": "th1", "turnId": f"tu{turns}", "item": {"type": "agentMessage", "text": f"Answer {turns}"}}})
        send({"method": "turn/completed", "params": {"threadId": "th1", "turn": {"id": f"tu{turns}", "status": "completed"}}})
    elif method == "turn/interrupt":
        send({"id": ident, "result": {}})
        send({"method": "turn/completed", "params": {"threadId": "th1", "turn": {"id": params["turnId"], "status": "interrupted"}}})
'''


class CodexServerEngineTest(Base):
    def test_one_process_one_thread_a_turn_per_message_and_stop_interrupts(self):
        import stat
        import time
        from assistant.conversations import Conversation
        folder = Path(self.temp.name)
        log = folder / "app-server.log"
        codex = folder / "codex"
        codex.write_text(f"#!{sys.executable}\nLOG = {str(log)!r}\n{FAKE_APP_SERVER}")
        codex.chmod(codex.stat().st_mode | stat.S_IXUSR)
        home = folder / "codex-home"
        home.mkdir()
        (home / "auth.json").write_text("{}")
        (home / "config.toml").write_text('[mcp_servers.cloudflare]\nurl = "https://example.com"\n[mcp_servers.docs-two]\ncommand = "x"\n')
        endpoint = {"name": "helper", "kind": "codex_sdk", "model": "gpt-5.6-luna", "reasoning_choice": "low"}

        def run(conversation, text):
            conversation.send(text)
            for _ in range(200):
                if not conversation.busy:
                    return
                time.sleep(0.02)
        with patch.dict(os.environ, {"FACULTY_CODEX_CLI": str(codex), "CODEX_HOME": str(home)}):
            conversation = Conversation(self.service, self.session, endpoint, "http://127.0.0.1:1", "#overview")
            run(conversation, "first")
            run(conversation, "second")
            conversation.send("hang on")
            time.sleep(0.3)
            conversation.stop()
            for _ in range(200):
                if not conversation.busy:
                    break
                time.sleep(0.02)
            process = conversation.engine.process
            conversation.close()
        lines = log.read_text().splitlines()
        arguments = json.loads(lines[0])
        self.assertEqual(arguments[:3], ["app-server", "--listen", "stdio://"])
        self.assertIn("mcp_servers.cloudflare.enabled=false", arguments)
        self.assertIn("mcp_servers.docs-two.enabled=false", arguments)
        self.assertIn('sandbox_mode="read-only"', arguments)
        sent = [json.loads(line) for line in lines[1:]]
        methods = [m.get("method") for m in sent if m.get("method")]
        self.assertEqual(methods.count("thread/start"), 1)
        self.assertEqual(methods.count("turn/start"), 3)
        self.assertIn("turn/interrupt", methods)
        thread = next(m for m in sent if m.get("method") == "thread/start")["params"]
        self.assertTrue(thread["ephemeral"])
        self.assertIn("Faculty assistant guide", thread["developerInstructions"])
        refused = [m for m in sent if m.get("id") and m["id"] >= 900 and "error" in m]
        self.assertEqual(len(refused), 3)
        first = next(m for m in sent if m.get("method") == "turn/start")["params"]
        self.assertIn("[I am on the page #overview.]", first["input"][0]["text"])
        self.assertEqual(first["effort"], "low")
        kinds = [(e["kind"], e.get("text", "")) for e in conversation.events if e["kind"] != "user"]
        self.assertEqual(kinds, [("assistant", "Answer 1"), ("assistant", "Answer 2"), ("status", "Stopped.")])
        self.assertIsNotNone(process.wait(timeout=5))

    def test_openrouter_and_vercel_run_through_codex_with_only_their_own_key(self):
        from assistant.engines import CodexServerEngine, HostedEngine, engine_for, gateway, provider_flags, EngineError
        openrouter = {**config.ENDPOINT, "name": "or", "kind": "hosted", "base_url": config.AGGREGATOR, "key_env": "OPENROUTER_API_KEY", "model": "deepseek/x"}
        vercel = {**openrouter, "base_url": config.VERCEL_GATEWAY, "key_env": "AI_GATEWAY_API_KEY"}
        other = {**openrouter, "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY"}
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "a", "AI_GATEWAY_API_KEY": "b", "EXAMPLE_API_KEY": "c"}):
            self.assertIsInstance(engine_for(openrouter), CodexServerEngine)
            self.assertEqual(engine_for(vercel).provider, {"name": "Vercel AI Gateway", "base_url": config.VERCEL_GATEWAY, "env_key": "AI_GATEWAY_API_KEY"})
            self.assertIsInstance(engine_for(other), HostedEngine)
            flags = provider_flags(gateway(openrouter))
            self.assertIn('model_providers.faculty_gateway.wire_api="responses"', flags)
            self.assertIn('model_providers.faculty_gateway.env_key="OPENROUTER_API_KEY"', flags)
            with self.assertRaises(EngineError):
                gateway({**openrouter, "key_env": "AI_GATEWAY_API_KEY"})  # a key bound to another host
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), self.assertRaises(EngineError):
            gateway(openrouter)

    def test_a_config_it_cannot_switch_off_refuses_codex(self):
        from assistant.engines import EngineError, _user_mcp_servers
        with tempfile.TemporaryDirectory() as home:
            Path(home, "config.toml").write_text('[mcp_servers."odd.name"]\ncommand = "x"\n')
            with patch.dict(os.environ, {"CODEX_HOME": home}), self.assertRaises(EngineError):
                _user_mcp_servers()


class SettingTest(unittest.TestCase):
    def test_the_assistant_model_is_any_known_connection_and_off_by_default(self):
        settings = config.validate({})
        self.assertEqual((settings["assistant_model"], config.assistant(settings)), ("", None))
        claude = {"name": "claude-sub", "kind": "agent_sdk", "model": "claude-sonnet-5-5", "base_url": "", "key_env": "", "modality": "vision", "lineage": ""}
        chosen = config.validate({"endpoints": [*config.DEFAULTS["endpoints"], claude], "assistant_model": "claude-sub", "assistant_reasoning": "high"})
        resolved = config.assistant(chosen)
        self.assertEqual((resolved["name"], resolved["reasoning_choice"]), ("claude-sub", "high"))
        self.assertEqual(config.validate({**chosen, "assistant_reasoning": "nonsense"})["assistant_reasoning"], "")
        with self.assertRaises(ValueError):
            config.validate({"assistant_model": "missing"})

    def test_the_coding_agent_is_a_subscription_or_a_gateway_model(self):
        gateway_model = {"name": "or-deepseek", "kind": "hosted", "base_url": config.AGGREGATOR, "key_env": "OPENROUTER_API_KEY", "model": "deepseek/x",
                         "modality": "text", "lineage": ""}
        custom = {**gateway_model, "name": "custom", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY"}
        endpoints = [*config.DEFAULTS["endpoints"], gateway_model, custom]
        chosen = config.validate({"endpoints": endpoints, "coding_model": "or-deepseek"})
        self.assertEqual(config.coding_agent(chosen)["model"], "deepseek/x")
        with self.assertRaises(ValueError):
            config.validate({"endpoints": endpoints, "coding_model": "custom"})
        self.assertEqual(config.coding_agent(config.validate({}))["kind"], "agent_sdk")


if __name__ == "__main__":
    unittest.main()
