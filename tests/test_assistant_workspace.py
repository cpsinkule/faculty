"""Assistant workspace additions: exact approvals, durable opt-in context, evidence,
read-only watches, managed files and bounded rule reads. All data is synthetic."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from assistant import documents, policy
from assistant.conversations import Conversation, Conversations
from course_storage import StorageError, inventory
from rosters import connect
from test_assistant import Base
from test_storage import HEADERS, temporary_storage
from browser_course_pages import seed_course
from app import create_app
import test_frq_grading as grading_fixture


class WorkspaceTests(Base):
    def conversation(self):
        class Engine:
            name = "test"
            def __init__(self):
                self.prompts = []
            def turn(self, conversation, prompt):
                self.prompts.append(prompt)
                conversation.emit("assistant", text="Continued using the result.")
            def close(self):
                pass
            def stop(self):
                pass
        engine = Engine()
        with patch("assistant.conversations.engine_for", return_value=engine):
            c = Conversation(self.service, self.session, {"name": "test"}, "http://localhost", "#overview")
        self.service.conversations._items[c.id] = c
        return c, engine

    def settled(self, c):
        until = time.monotonic() + 5
        while c.busy and time.monotonic() < until:
            time.sleep(.01)
        self.assertFalse(c.busy)

    def test_pending_cards_and_count_survive_more_than_one_hundred_newer_actions(self):
        first = self.write("POST", "/api/tools", {"name": "Still waiting", "url": "https://example.test"})["action_id"]
        for number in range(120):
            aid = self.service._record_action(self.session, "auto", "workspace_calendar.create", "POST", "/api/calendar/events", {}, str(number))
            self.service._event(aid, "succeeded", "assistant", {})
        view = self.actions()
        self.assertEqual([a["id"] for a in view["pending"]], [first])
        self.assertEqual(self.client.get("/api/assistant/status").json["pending"], 1)
        ids = [a["id"] for a in view["recent"]]
        while view["next_before"]:
            view = self.client.get(f"/api/assistant/actions?before={view['next_before']}").json
            self.assertEqual([a["id"] for a in view["pending"]], [first])
            ids += [a["id"] for a in view["recent"]]
        self.assertEqual(len(ids), 120)
        self.assertEqual(len(set(ids)), 120)

    def test_receipt_continues_once_after_all_cards_without_resubmitting(self):
        c, engine = self.conversation()
        c.transcript.append(("Instructor", "Add two links, then tell me they are there."))
        ids = [self.write("POST", "/api/tools", {"name": f"Link {n}", "url": "https://example.test"})["action_id"] for n in range(2)]
        for number, aid in enumerate(ids):
            card = self.service._view(*self.service.action(aid))
            result = self.client.post(f"/api/assistant/actions/{aid}/approve", json={"seen": card["seen"]}, headers=HEADERS)
            self.assertEqual(result.status_code, 200)
            if number == 0:
                self.assertEqual(engine.prompts, [])
        self.settled(c)
        self.service.conversations.resolved()
        self.assertEqual(len(engine.prompts), 1)
        self.assertIn('"status": "succeeded"', engine.prompts[0])
        self.assertIn("Never repeat submitted writes", engine.prompts[0])
        self.assertEqual(len(self.actions()["recent"]), 2)

    def test_receipts_of_a_large_group_continue_shortened(self):
        # ONE CARD FOR ONE CHANGE: 60 approved grade changes, each answered with a whole response record, once overflowed the
        # 200,000-character continuation and ended the task with an error. Each receipt keeps its outcome, status and error.
        c, engine = self.conversation()
        ids = [self.write("POST", "/api/tools", {"name": f"Link {n}", "url": "https://example.test"})["action_id"] for n in range(60)]
        batch = self.service.batch(self.session, ids, "Links")
        seen = {aid: self.service._view(*self.service.action(aid))["seen"] for aid in ids}
        answers = [(200, {"record": "x" * 6000})] * 59 + [(409, {"error": "The grade changed meanwhile."})]
        with patch.object(self.service.transport, "request", side_effect=[(status, data, None) for status, data in answers]):
            self.client.post(f"/api/assistant/batches/{batch['batch_id']}/approve", json={"seen": seen}, headers=HEADERS)
        self.settled(c)
        self.assertEqual(len(engine.prompts), 1)
        prompt = engine.prompts[0]
        self.assertLess(len(prompt), 60000)
        self.assertTrue(all(aid in prompt for aid in ids))
        self.assertEqual(prompt.count('"status": "succeeded"'), 59)
        self.assertIn("The grade changed meanwhile.", prompt)
        self.assertIn("60 receipts are shortened", prompt)
        self.assertNotIn("xxxxxxxx", prompt)

    def test_busy_or_paused_task_waits_to_continue(self):
        c, engine = self.conversation()
        c.busy = True
        aid = self.write("POST", "/api/tools", {"name": "one", "url": "https://example.test"})["action_id"]
        self.service.decline(aid)
        self.service.conversations.resolved()
        self.assertEqual(engine.prompts, [])
        c.busy = False
        self.service.store.save(c, {"state": "paused", "title": "Task"})
        c.continue_if_ready()
        self.assertEqual(engine.prompts, [])
        self.service.store.save(c, {"state": "active"})
        c.continue_if_ready()
        self.settled(c)
        self.assertEqual(len(engine.prompts), 1)
        self.assertIn('"status": "declined"', engine.prompts[0])

    def test_saved_context_is_opt_in_reopens_and_can_be_deleted(self):
        c, engine = self.conversation()
        c.emit("assistant", text="An unsaved answer")
        self.assertEqual(self.service.store.listing()["conversations"], [])
        c.ask([{"title": "Which term?", "options": ["Fall", "Spring"]}])
        self.service.store.save(c, {"title": "Readiness", "goal": "Check the exam", "retention_days": 7})
        c.persist()
        raw = self.service.store.load(c.id)
        self.assertNotIn(self.session["token"], raw["snapshot_json"])
        self.service.conversations = Conversations(self.service)
        with patch.object(self.service.conversations, "_settings", return_value={}), \
             patch("assistant.conversations.config.assistant", return_value={"name": "test"}), \
             patch("assistant.conversations.engine_for", return_value=engine):
            reopened = self.service.conversations.reopen(c.id)
        self.assertEqual(reopened["question"], c.question)
        self.assertEqual(reopened["saved"]["goal"], "Check the exam")
        self.assertEqual(reopened["events"][0]["text"], "An unsaved answer")
        self.assertEqual(self.client.delete(f"/api/assistant/saved/{c.id}", json={}, headers=HEADERS).status_code, 200)
        self.service.conversations.get(c.id).persist()
        self.assertEqual(self.service.store.listing()["conversations"], [])

    def test_expiry_and_explicit_preferences_with_revision_checks(self):
        c, _ = self.conversation()
        self.service.store.save(c, {"title": "Old"})
        with connect(self.service.database) as db:
            db.execute("UPDATE assistant_saved_conversations SET expires_at='2000-01-01' WHERE id=?", (c.id,))
        self.assertEqual(self.service.store.listing()["conversations"], [])
        from test_assistant_memory import answering  # EVERY WAY IN: a preference written by hand is checked as a chat message is
        with answering():
            first = self.service.store.memory({"title": "Style", "content": "Use concise answers."})
            with self.assertRaises(Conflict):
                self.service.store.memory({"title": "Style", "content": "Different", "revision": 0}, first["id"])
            second = self.service.store.memory({"title": "Style", "content": "Use tables.", "revision": first["revision"]}, first["id"])
        self.assertIn("Use tables", c.context_note())
        self.service.store.forget_memory(first["id"], second["revision"])
        self.assertEqual(self.service.store.memories(), [])

    def test_question_accepts_free_text_once_and_never_approves(self):
        c, engine = self.conversation()
        result, _ = self.tool("ask_question", questions=[{"title": "Which section?", "options": ["507", "508"]}])
        self.assertEqual(result["outcome"], "waiting_for_input")
        question = c.question["id"]
        result = self.client.post(f"/api/assistant/conversations/{c.id}/questions/{question}", json={"answers": {"0": "Both sections"}}, headers=HEADERS)
        self.assertEqual(result.status_code, 200)
        self.settled(c)
        self.assertIn("Both sections", engine.prompts[0])
        with self.assertRaises(Conflict):
            c.answer(question, {"0": "507"})
        for endpoint in ("assistant.answer_question", "assistant.approve_batch", "assistant.memory_create", "assistant.save_conversation"):
            self.assertEqual(policy.base_rule(endpoint).tier, "never")
        self.assertEqual(self.actions()["recent"], [])

    def test_new_message_supersedes_queued_task_and_open_question(self):
        c, engine = self.conversation()
        c._continuations = ["Earlier action succeeded."]
        c.ask([{"title": "Choose a course"}])
        c.send("Cancel that task and answer this instead.")
        self.settled(c)
        self.assertEqual(len(engine.prompts), 1)
        self.assertTrue(engine.prompts[0].endswith("Cancel that task and answer this instead."))
        self.assertIsNone(c.question)
        self.assertEqual(c._continuations, [])

    def test_batch_preflights_every_card_and_records_partial_outcomes(self):
        ids = [self.write("POST", "/api/tools", {"name": name, "url": "https://example.test"})["action_id"] for name in ("First", "", "Third")]
        batch = self.service.batch(self.session, ids, "Links")
        seen = {aid: self.service._view(*self.service.action(aid))["seen"] for aid in ids}
        bad = {**seen, ids[-1]: seen[ids[-1]] + 1}
        with self.assertRaises(Conflict):
            self.service.approve_batch(batch["batch_id"], bad)
        self.assertEqual(len(self.actions()["pending"]), 3)
        result = self.service.approve_batch(batch["batch_id"], seen)
        self.assertEqual([r["action"]["status"] for r in result["results"]], ["succeeded", "failed"])
        self.assertEqual(result["remaining"], [ids[-1]])
        self.assertEqual([a["id"] for a in self.actions()["pending"]], [ids[-1]])

    def test_batch_stale_preview_submits_nothing(self):
        initial = {"status": 200, "data": {}, "fingerprint": "first"}
        with patch.object(self.service, "_preview", return_value=initial):
            ids = [self.write("POST", "/api/mail/send", {"subject": str(n)})["action_id"] for n in range(2)]
        batch = self.service.batch(self.session, ids, "Mail")
        seen = {aid: self.service._view(*self.service.action(aid))["seen"] for aid in ids}
        with patch.object(self.service, "_preview", side_effect=[initial, {**initial, "fingerprint": "changed"}]), \
             patch.object(self.service.transport, "request") as submit:
            result = self.service.approve_batch(batch["batch_id"], seen)
        self.assertTrue(result["stale"])
        submit.assert_not_called()
        self.assertEqual(len(self.actions()["pending"]), 2)

    def test_batch_names_cards_that_already_went_through(self):
        # ALREADY APPROVED (ASSISTANT-BATCHES, 2026-10-06): a second Approve all from a page drawn mid-run submits nothing again.
        ids = [self.write("POST", "/api/tools", {"name": name, "url": "https://example.test"})["action_id"] for name in ("First", "", "Third")]
        batch = self.service.batch(self.session, ids, "Links")
        seen = {aid: self.service._view(*self.service.action(aid))["seen"] for aid in ids}
        first = self.service.approve_batch(batch["batch_id"], seen)
        self.assertEqual([r["action"]["status"] for r in first["results"]], ["succeeded", "failed"])
        with patch.object(self.service.transport, "request") as submit:
            again = self.service.approve_batch(batch["batch_id"], {ids[0]: seen[ids[0]]})
            self.assertEqual((again["already"], again["results"], again["remaining"], again["stale"]), ([ids[0]], [], [], False))
            self.assertEqual(again["message"], "Already approved. Nothing was submitted again.")
            with self.assertRaisesRegex(Conflict, ": 1 of these steps was already approved. Review the steps that still wait; nothing was submitted"):
                self.service.approve_batch(batch["batch_id"], {ids[0]: seen[ids[0]], ids[2]: seen[ids[2]]})
            with self.assertRaisesRegex(Conflict, "changed or expired"):  # a failed card is no approval to skip
                self.service.approve_batch(batch["batch_id"], {ids[1]: seen[ids[1]], ids[2]: seen[ids[2]]})
            submit.assert_not_called()
        self.assertEqual([a["id"] for a in self.actions()["pending"]], [ids[2]])

    def test_one_route_group_is_one_card_for_many_changes(self):
        # ONE CARD FOR ONE CHANGE (ASSISTANT-BATCHES, requirement, 2026-10-09): one grade change for 119 students came as cards in
        # groups of 20 at most. One route groups up to 200, different routes stay at 20, and a card named again moves to the new group.
        ids = [self.write("POST", "/api/tools", {"name": f"Link {n}", "url": "https://example.test"})["action_id"] for n in range(30)]
        parts = [self.tool("group_actions", title="Links, part", action_ids=part)[0] for part in (ids[:20], ids[20:])]
        self.assertEqual([len(p["action_ids"]) for p in parts], [20, 10])
        whole, _ = self.tool("group_actions", title="Teaching links", action_ids=ids)
        self.assertEqual(whole["note"], "The instructor sees one summary card with Approve all.")
        self.assertEqual([(b["title"], b["action_ids"], b["route"], b["pending"], b["states"]) for b in self.actions()["batches"]],
                         [("Teaching links", ids, "add_tool", 30, {"proposed": 30})])
        other_route = [self.write("DELETE", f"/api/calendar/events/e{n}", {})["action_id"] for n in range(2)]
        with self.assertRaisesRegex(ValueError, "up to 20 different kinds of change"):
            self.service.batch(self.session, ids[:19] + other_route, "Mixed")
        mixed = self.service.batch(self.session, ids[:2] + other_route, "Mixed")
        self.assertEqual(mixed["note"], "The instructor reviews each card of the group.")
        self.assertEqual([(b["title"], len(b["action_ids"]), b["route"]) for b in self.actions()["batches"]],
                         [("Teaching links", 28, "add_tool"), ("Mixed", 4, None)])
        with self.assertRaisesRegex(ValueError, "2 to 200"):
            self.service.batch(self.session, [f"x{n}" for n in range(201)], "Too many")
        with self.assertRaisesRegex(ValueError, "pending actions from this conversation"):
            self.service.batch(self.service.open_session("test"), ids[2:4], "Not mine")
        # Approve all: every card preflighted, then each submitted in order, each with its own receipt.
        seen = {aid: self.service._view(*self.service.action(aid))["seen"] for aid in ids[2:]}
        result = self.client.post(f"/api/assistant/batches/{whole['batch_id']}/approve", json={"seen": seen}, headers=HEADERS).json
        self.assertEqual([r["id"] for r in result["results"]], ids[2:])
        self.assertEqual({r["action"]["status"] for r in result["results"]}, {"succeeded"})
        self.assertEqual(result["remaining"], [])
        self.assertEqual([(b["title"], b["pending"], b["states"]) for b in self.actions()["batches"]],
                         [("Teaching links", 0, {"succeeded": 28}), ("Mixed", 4, {"proposed": 4})])

    def test_decline_all_declines_only_the_cards_still_waiting(self):
        ids = [self.write("POST", "/api/tools", {"name": f"Link {n}", "url": "https://example.test"})["action_id"] for n in range(3)]
        batch = self.service.batch(self.session, ids, "Links")
        self.service.decline(ids[0])
        self.service.approve_batch(batch["batch_id"], {ids[1]: self.service._view(*self.service.action(ids[1]))["seen"]})
        result = self.client.post(f"/api/assistant/batches/{batch['batch_id']}/decline", json={}, headers=HEADERS).json
        self.assertEqual((result["declined"], result["message"]), ([ids[2]], "Declined 1 change."))
        self.assertEqual([self.service._view(*self.service.action(a))["status"] for a in ids], ["declined", "succeeded", "declined"])
        again = self.client.post(f"/api/assistant/batches/{batch['batch_id']}/decline", json={}, headers=HEADERS).json
        self.assertEqual((again["declined"], again["message"]), ([], "Nothing in this group was still waiting."))
        self.assertEqual(self.client.post("/api/assistant/batches/unknown/decline", json={}, headers=HEADERS).status_code, 404)
        self.assertEqual(policy.base_rule("assistant.decline_batch").tier, "never")

    def test_only_a_send_mail_accepted_counts_as_already_approved(self):
        from assistant.service import _went_through
        sent = lambda status, endpoint="gmail_mail.send", result=None: {"pending": False, "status": status, "endpoint": endpoint,
                                                                          "events": [{"detail": {"result": result} if result else None}]}
        self.assertTrue(_went_through(sent("succeeded", result={"status": "accepted"})))
        self.assertFalse(_went_through(sent("succeeded", result={"status": "uncertain"})))  # an unconfirmed send still stops a run
        self.assertTrue(_went_through(sent("succeeded", "workspace_tools.create")))
        for status in ("approved", "failed", "declined", "expired"):
            self.assertFalse(_went_through(sent(status, "workspace_tools.create")))
        self.assertFalse(_went_through({"pending": True, "status": "proposed", "endpoint": "x", "events": []}))

    def test_evidence_records_actual_read_and_changes_with_source(self):
        first, event = self.tool("courses")
        evidence = event["evidence"][0]
        self.assertEqual(evidence, first["_evidence"][0])
        self.assertEqual(evidence["path"], "/api/state?include=core")
        self.assertEqual(len(evidence["fingerprint"]), 64)
        seed_course(self.client, sections=[507, 508])
        _, later = self.tool("courses")
        self.assertNotEqual(evidence["fingerprint"], later["evidence"][0]["fingerprint"])

    def test_docs_and_long_source_pages_are_complete_and_detect_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.py"
            path.write_text("x = '" + "a" * 15000 + "'\nsecond = 2\n")
            args = {"path": "source.py", "start": 1, "lines": 2}
            result = documents.source(path, args)
            output = result["text"]
            self.assertFalse(result["last_line_complete"])
            while result["next_offset"] is not None:
                args.update(offset=result["next_offset"], fingerprint=result["fingerprint"])
                result = documents.source(path, args)
                output += result["text"]
            self.assertEqual(output, "1: " + path.read_text().splitlines()[0] + "\n2: second = 2")
            self.assertEqual(result["end"], 2)
            path.write_text(path.read_text() + "third = 3\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                documents.source(path, args)
        result = documents.read({"name": "AGENTS.md", "section": "TA contacts"})
        self.assertIn("Rule ID: **TA-CONTACTS**", result["text"])

    def test_every_housekeeping_retry_resolves_certification(self):
        for endpoint, args in (("frq_grading.retry_preparation", {"identifier": "p"}), ("documents.decode_retry", {"message_id": "m"}),
                               ("documents.alias_ocr_retry", {"scan_id": "s"}), ("documents.version_sort_retry", {"message_id": "m"}),
                               ("inbox.change_job", {"identifier": "j", "operation": "retry"})):
            with patch.object(self.service.context, "retry_assignments", return_value=["a"]), patch.object(self.service.context, "ever_certified", return_value=True):
                rule = policy.classify(endpoint, "POST", args, {}, self.service.context)
            self.assertEqual(rule.tier, "approve", endpoint)
            self.assertIn("ASSISTANT-CERTIFIED", rule.reason)


class WatchTests(Base):
    def observation(self, ready=False):
        return {"ready": ready, "finished": ready, "needs_attention": False, "page": "#grading/plan", "message": "Ready" if ready else "Working"}

    def create(self):
        data = {"title": "Exam grading", "kind": "grading", "target": "plan", "condition": "ready", "request_id": "watch-one"}
        with patch.object(self.service.watches, "observe", return_value=self.observation()):
            preview = self.service.watches.preview(data)
            return self.service.watches.create({**data, "fingerprint": preview["fingerprint"]})

    def due(self):
        with connect(self.service.database) as db:
            db.execute("UPDATE assistant_watches SET next_check=0")

    def test_quiet_until_transition_once_then_failure_and_recovery(self):
        watch = self.create()
        self.assertFalse(self.service.watches.process_one())
        for state in (False, True, True):
            self.due()
            with patch.object(self.service.watches, "observe", return_value=self.observation(state)):
                self.assertTrue(self.service.watches.process_one())
        notices = self.service.watches.listing()["notifications"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["message"], "Ready")
        for _ in range(2):
            self.due()
            with patch.object(self.service.watches, "observe", side_effect=ValueError("Source unavailable")):
                self.service.watches.process_one()
        self.assertEqual(len(self.service.watches.listing()["notifications"]), 2)
        self.due()
        with patch.object(self.service.watches, "observe", return_value=self.observation(True)):
            self.service.watches.process_one()
        self.assertEqual(len(self.service.watches.listing()["notifications"]), 3)
        self.service.watches.update(watch["id"], {"revision": 1, "enabled": False})
        self.due()
        self.assertFalse(self.service.watches.process_one())

    def test_pause_during_read_suppresses_obsolete_notification(self):
        watch = self.create()
        self.due()
        def reading(*args):
            self.service.watches.update(watch["id"], {"revision": 1, "enabled": False})
            return self.observation(True)
        with patch.object(self.service.watches, "observe", side_effect=reading):
            self.service.watches.process_one()
        self.assertEqual(self.service.watches.listing()["notifications"], [])

    def test_deleted_scan_never_reports_finished_and_reads_are_bounded(self):
        with patch.object(self.service.watches, "read", return_value={"deleted": {"at": "today"}, "status": "completed"}):
            with self.assertRaisesRegex(ValueError, "removed"):
                self.service.watches.observe("scan", "job")
        for bad in ("../grades", "https://outside.test", "a?reveal=1"):
            with self.assertRaises(ValueError):
                self.service.watches.preview({"kind": "scan", "target": bad, "condition": "finished", "title": "x"})


class ArtifactTests(Base):
    def test_files_belong_to_exact_retained_owner_and_retry_is_idempotent(self):
        seed_course(self.client, sections=[507, 508])
        with connect(self.service.database) as db:
            ids = {level: db.execute(f"SELECT id FROM {table} LIMIT 1").fetchone()[0] for level, table in
                   (("definition", "course_definitions"), ("term", "course_terms"), ("section", "course_sections"))}
        for level, ident in ids.items():
            data = {"level": level, "owner_id": ident, "title": "Feedback", "filename": "feedback.txt", "content": "Carefully justified.", "request_id": level}
            preview = self.service.artifacts.preview(data)
            data["fingerprint"] = preview["fingerprint"]
            first = self.service.artifacts.create(data)
            self.assertEqual(first["id"], self.service.artifacts.create(data)["id"])
            response = self.client.get(first["url"])
            self.assertEqual(response.data, b"Carefully justified.")
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(first[level + "_id"], ident)
            with self.assertRaises(Conflict):
                self.service.artifacts.create({**data, "content": "Changed"})
            with connect(self.service.database) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE assistant_artifacts SET title='changed' WHERE id=?", (first["id"],))
        self.assertEqual(len(self.service.artifacts.listing()["artifacts"]), 3)
        with self.assertRaises(ValueError):
            self.service.artifacts.preview({**data, "filename": "../escape.txt"})

    def test_artifact_tool_waits_for_approval_and_fetches_saved_file(self):
        seed_course(self.client, sections=[507, 508])
        with connect(self.service.database) as db:
            ident = db.execute("SELECT id FROM course_definitions").fetchone()[0]
        proposed, _ = self.tool("create_artifact", level="definition", owner_id=ident, title="Worksheet", filename="worksheet.pdf", content="1. Explain your method.\n\n2. Check your answer.")
        self.assertEqual(proposed["outcome"], "waiting_for_approval", proposed)
        self.assertEqual(self.service.artifacts.listing()["artifacts"], [])
        card = self.actions()["pending"][0]
        result = self.service.approve(card["id"], card["seen"])
        self.assertEqual(result["status"], 201, result)
        self.assertTrue(self.client.get(result["result"]["url"]).data.startswith(b"%PDF"))
        fetched, event = self.tool("fetch_files", paths=[result["result"]["url"]])
        self.assertTrue(event["ok"], fetched)
        self.assertIn("sha256", event["evidence"][0]["versions"])
        # Never silently replace an unsupported character with a square in the PDF.
        with self.assertRaisesRegex(ValueError, "cannot render"):
            self.service.artifacts.preview({"level": "definition", "owner_id": ident, "title": "Unicode", "filename": "unicode.pdf", "content": "\U0010ffff"})

    def test_backup_checks_recorded_file_hashes_for_all_three_owners(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = temporary_storage(folder)
            app = create_app(storage=storage)
            seed_course(app.test_client(), sections=[507, 508])
            service = app.extensions["assistant"]
            with connect(service.database) as db:
                ids = {level: db.execute(f"SELECT id FROM {table}").fetchone()[0] for level, table in
                       (("definition", "course_definitions"), ("term", "course_terms"), ("section", "course_sections"))}
            for level, ident in ids.items():
                data = {"level": level, "owner_id": ident, "title": "Retained", "filename": "notes.txt", "content": "Original", "request_id": level}
                data["fingerprint"] = service.artifacts.preview(data)["fingerprint"]
                artifact = service.artifacts.create(data)
                row = service.artifacts.row(artifact["id"])
                manager = getattr(storage, level + "_storage")
                owner = manager.row(ident)
                with service.rosters.lock(), service.artifacts.files(service.artifacts.owner(level, ident), write=True) as root:
                    manager.check_source(owner, root.parent, inventory(root.parent))
                    (root / row["path"]).write_text("Corrupted")
                    with self.assertRaises(StorageError):
                        manager.check_source(owner, root.parent, inventory(root.parent))
                    (root / row["path"]).write_text("Original")
            # A JSON download named sources.json is ordinary generated content, not an assignment's source manifest.
            data = {"level": "definition", "owner_id": ids["definition"], "title": "Sources", "filename": "sources.json", "content": '{"references": []}\n', "request_id": "sources"}
            data["fingerprint"] = service.artifacts.preview(data)["fingerprint"]
            artifact = service.artifacts.create(data)
            session = service.open_session("test")
            result, event = service.run_tool(session, "fetch_files", {"paths": [artifact["url"]], "look": True})
            self.assertTrue(event["ok"], result)
            self.assertEqual(storage.backup_all()["status"], "complete")


class OwnershipResolutionTests(unittest.TestCase):
    managed = False
    setUp = grading_fixture.FrqWorkflowTests.setUp
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric

    def test_real_preparation_and_scan_ownership_resolve_to_term_plan(self):
        service = self.app.extensions["assistant"]
        preparation = self.frq.queue_preparation(self.plan["id"], "source", {}, "assistant-ownership")
        self.assertEqual(service.context.retry_assignments("frq_grading.retry_preparation", {"identifier": preparation["id"]}), [self.plan["id"]])
        message = self.job["message_id"]
        for endpoint, args in (("documents.decode_retry", {"message_id": message}),
                               ("inbox.change_job", {"identifier": self.job["id"]})):
            self.assertIn(self.plan["id"], service.context.retry_assignments(endpoint, args))
        with connect(self.path) as db:
            page_id = db.execute("SELECT id FROM scan_pages WHERE message_id=? LIMIT 1", (message,)).fetchone()[0]
        self.assertIn(self.plan["id"], service.context.retry_assignments("documents.alias_ocr_retry", {"scan_id": page_id}))
        # The real certification projection is readable and readiness uses its own blocking count.
        observed = service.watches.observe("grading", self.plan["id"])
        self.assertFalse(observed["ready"])
        self.assertGreater(observed["blocking"], 0)
