"""Read-only Home summaries, honest operational evidence and fair queue turns."""
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import uuid

from course_storage import StorageError, timestamp
from rosters import connect
from test_inbox import InboxTestCase
from test_storage import HEADERS
import workspace_overview
from workspace_overview import register, storage_status
import test_certified_archive as archive_fixture
import test_documents as documents_fixture
import test_frq_grading as frq_fixture
from frq_fixtures import finalize_rubrics
import test_grading_cloud as cloud_fixture
import test_grading_locations as locations_fixture
import test_seating_updates as seat_fixture


# The queue predicates before 2026-10-02 (bugs-backend-2), with today's labels: when nothing has failed, the predicates that
# discount superseded failures must change nothing in the response.
EARLIER_PREDICATES = {"alias_ocr": "1=1", "frq_grading": "1=1", "frq_preparation": "1=1", "frq_rubric_quality": "q.cancelled=0",
                      "frq_rubric_drafts": "q.cancelled=0", "frq_locations": "1=1"}
EARLIER_SPECS = tuple((key, label, table, EARLIER_PREDICATES.get(key, where), href, message)
                      for key, label, table, where, href, message in workspace_overview.QUEUE_SPECS)


def without_time(summary):
    """The response but its clock and the drives' free bytes, which change between two reads."""
    found = {key: value for key, value in summary.items() if key != "generated_at"}
    found["operations"] = {**found["operations"], "storage": None}
    return found


class OverviewTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        if "workspace_overview" not in self.app.blueprints:
            register(self.app)

    def get_overview(self):
        response = self.client.get("/api/workspace/overview")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json

    def test_summary_counts_and_wait_age_do_not_expose_commands_or_payloads(self):
        job = self.submit(filename="Sensitive student 001234567.txt", content=b"Private original",
                          sender="private-instructor@example.test")
        before = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET created_at=? WHERE id=?", (before, job["id"]))
        with patch.object(self.inbox, "read_original", side_effect=AssertionError("Must not read originals")), \
             patch.object(self.inbox, "read_artifact", side_effect=AssertionError("Must not read payloads")):
            summary = self.get_overview()
        self.assertEqual(summary["operations"]["totals"]["pending"], 1)
        queue = next(q for q in summary["operations"]["queues"] if q["key"] == "inbox")
        self.assertGreaterEqual(queue["oldest_waiting_seconds"], 600)
        self.assertEqual(summary["operations"]["worker"]["state"], "unknown")
        self.assertEqual(summary["operations"]["storage"]["enabled"], self.managed)
        for secret in ("Sensitive student", "001234567", "private-instructor", "Private original", "parameters_json", "token"):
            self.assertNotIn(secret, json.dumps(summary))
        self.assertEqual(self.inbox.get(job["id"])["status"], "pending")

    def test_scan_summaries_are_bounded_and_use_the_existing_overview(self):
        scans = []
        for number in range(8):
            scans.append(self.submit({"action": "scan.receive"}, filename="scan.pdf", content=b"%%PDF-fixture-%d" % number,
                                     instructor_review_required=True))
        self.submit()  # A queued command: it computes no scan summary.
        service = self.app.extensions["document_scans"]
        with patch.object(service, "overview", wraps=service.overview) as read:
            summary = self.get_overview()
        # One summary per present scan stored as needs_review (what files it, as the Inbox list does), reused for every list.
        self.assertEqual(read.call_count, 8)
        self.assertEqual(len(summary["recent_scans"]), 6)
        # Fresh scans are the worker's (being inspected), so the Inbox files them under Running: nothing needs the instructor.
        self.assertNotIn("#inbox", [item["href"] for item in summary["attention"]])
        self.assertEqual(len(summary["current_scans"]), 8)
        self.assertEqual(summary["current_scans_total"], 8)
        self.assertEqual({scan["listed_as"] for scan in summary["current_scans"]}, {"running"})
        self.assertEqual(summary["finished_recent"], 0)
        for scan in summary["recent_scans"] + summary["current_scans"]:
            with connect(self.inbox.path) as db:
                self.assertEqual(scan["scan"], service.overview(db, self.inbox.get(scan["id"])["message_id"]))
            self.assertEqual(scan["href"], "#inbox/job/" + scan["id"])
        self.assertEqual(summary["current_scans"][0]["pages"], {"done": 0, "total": None})
        self.assertNotIn("scan.pdf", json.dumps(summary))
        self.assertTrue(all(self.inbox.get(s["id"])["instructor_review_required"] for s in scans))

    def test_inbox_counts_are_the_inbox_filing_and_deleted_scans_count_nowhere(self):
        states = {}
        def scan(state, number):
            job = self.submit({"action": "scan.receive"}, filename="private-name.pdf", content=b"%%PDF-state-%d" % number,
                              instructor_review_required=True)
            states[job["message_id"]] = state
            return job
        todo, check, done, wait, deleted = (scan(state, n) for n, state in enumerate(("todo", "check", "done", "wait", "todo")))
        unauthenticated = self.submit(filename="other.txt", content=b"Other", authenticated=False)
        broken = self.submit(filename="broken.txt", content=b"Broken")
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET status='failed' WHERE id=?", (broken["id"],))
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)",
                       (deleted["message_id"], "workspace", "test", timestamp()))
        service = self.app.extensions["document_scans"]
        fake = lambda db, message_id, *args, **kwargs: {"state": states[message_id], "headline": "Quiz", "steps": [], "stages": [], "facts": []}
        with patch.object(service, "overview", side_effect=fake):
            summary = self.get_overview()
            listing = self.inbox.listing("current")
        card = next(item for item in summary["attention"] if item["href"] == "#inbox")
        # todo + check scans, the unauthenticated submission waiting for review and the failed one; never the deleted scan.
        self.assertEqual((card["count"], card["scans"], card["submissions"], card["derived"]), (4, 2, 2, True))
        self.assertEqual(card["count"], listing["counts"].get("needs_review", 0) + listing["counts"].get("failed", 0))
        self.assertEqual(card["label"], "Scans and submissions that need you")
        self.assertEqual(summary["finished_recent"], 1)
        self.assertNotIn(deleted["id"], [job["id"] for job in summary["recent_scans"]])
        self.assertEqual([job["id"] for job in summary["current_scans"]], [check["id"], todo["id"], wait["id"]])
        self.assertEqual([job["listed_as"] for job in summary["current_scans"]], ["needs_review", "needs_review", "running"])
        self.assertEqual(card["submissions_failed"], 1)
        # The health table's Inbox row is filed like the card (review W2-HOME-D1): Review + Failed is the card's count, the
        # scan the worker still has is Running, and the finished and deleted scans are nowhere.
        queue = next(q for q in summary["operations"]["queues"] if q["key"] == "inbox")
        self.assertEqual((queue["needs_review"], queue["failed"], queue["running"], queue["pending"]), (3, 1, 1, 0))
        self.assertEqual(queue["needs_review"] + queue["failed"], card["count"])
        # Totals of queued and running work stay the worker's own (stored statuses): the waiting scan's work is its queue's.
        self.assertEqual(summary["operations"]["totals"]["running"], 0)
        self.assertEqual(summary["operations"]["totals"]["needs_review"], sum(q["needs_review"] for q in summary["operations"]["queues"]))
        self.assertEqual(summary["current_grading_plans"], [])
        self.assertNotIn("private-name", json.dumps(summary))
        # Nothing was written: the stored statuses and the review gate are untouched.
        self.assertEqual({self.inbox.get(job["id"])["status"] for job in (todo, check, done, wait, deleted)}, {"needs_review"})
        self.assertEqual(self.inbox.get(unauthenticated["id"])["status"], "needs_review")
        # A deleted scan's failed inspection is not a failure anyone needs to look at any more.
        with connect(self.inbox.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='failed' WHERE message_id=?", (deleted["message_id"],))
        with patch.object(service, "overview", side_effect=fake):
            summary = self.get_overview()
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "scans")["failed"], 0)
        self.assertNotIn("queue:scans:failed", [item.get("key") for item in summary["attention"]])

    def test_a_present_failed_inspection_is_the_inbox_cards_alone_and_stays_in_the_health_table(self):
        """Review fix (2026-10-02): the scan's own Inbox row and a 'Scan inspection: failed' card counted one failure twice."""
        job = self.submit({"action": "scan.receive"}, filename="scan.pdf", content=b"%%PDF-failed-inspection", instructor_review_required=True)
        with connect(self.inbox.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='failed',error='Synthetic failure' WHERE message_id=?", (job["message_id"],))
            db.execute("INSERT INTO inbox_worker VALUES(1,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at", (timestamp(),))
        summary = self.get_overview()
        self.assertEqual([item["key"] for item in summary["attention"] if item["key"] not in ("storage", "disk")], ["inbox"])
        self.assertEqual(summary["attention"][0]["scans"], 1)
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "scans")["failed"], 1)

    def test_nothing_failed_keeps_the_response_of_the_earlier_queue_rules(self):
        for number in range(2):
            self.submit({"action": "scan.receive"}, filename="scan.pdf", content=b"%%PDF-same-%d" % number, instructor_review_required=True)
        self.submit()
        current = without_time(self.get_overview())
        with patch("workspace_overview.QUEUE_SPECS", EARLIER_SPECS):
            self.assertEqual(without_time(self.get_overview()), current)

    def test_listing_counts_are_unchanged_by_the_shared_filing(self):
        for number, state in enumerate(("todo", "done", "wait")):
            self.submit({"action": "scan.receive"}, filename="scan.pdf", content=b"%%PDF-listing-%d" % number, instructor_review_required=True)
        self.submit()
        service = self.app.extensions["document_scans"]
        with patch.object(service, "overview", wraps=service.overview) as read:
            listing = self.inbox.listing()
        self.assertEqual(read.call_count, 3)  # The Inbox list keeps a summary for every present scan.
        self.assertEqual(sum(listing["counts"].values()), 4)
        self.assertTrue(all("listed_as" in job for job in listing["jobs"] if job["action"] == "scan.receive"))

    def test_upcoming_exams_use_current_term_and_keep_wall_clock_time(self):
        exams = self.app.extensions["exams"]
        catalog = exams.catalog()
        term = catalog["terms"][0]
        section = next(s for s in catalog["sections"] if s["term_id"] == term["id"])
        assignment = self.app.extensions["documents"].create_assignment(term["definition_id"], "overview-exam", "Upcoming test")
        event = exams.create({"term_id": term["id"], "assignment_id": assignment["id"], "section_ids": [section["id"]]})
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        event = exams.save_sitting(event["id"], {"version": event["version"], "label": "Classroom", "kind": "main",
                                   "date": tomorrow, "time": "08:35", "location": "HECC 105"})
        result = self.get_overview()["upcoming_exams"]
        self.assertEqual([(s["date"], s["time"], s["term_label"]) for s in result], [(tomorrow, "08:35", "Fall 2026")])
        self.assertEqual(result[0]["href"], "#exams/" + event["id"])
        self.change_course(self.course, is_current=False)
        self.assertEqual(self.get_overview()["upcoming_exams"], [])

    def test_new_bubble_attempt_supersedes_failed_queue_alert(self):
        job = self.submit({"action": "scan.receive"}, filename="scan.pdf", content=b"%PDF-fixture", instructor_review_required=True)
        assignment = self.app.extensions["documents"].create_assignment(self.definition["id"], "bubble-overview", "Bubble exam")
        with connect(self.inbox.path) as db:
            for key, status in (("older", "failed"), ("newer", "complete")):
                db.execute("""INSERT INTO bubble_runs(id,message_id,assignment_id,term_id,question_count,profile,status,actor,created_at,updated_at)
                    VALUES(?,?,?,?,1,'test',?,'workspace',?,?)""", (key, job["message_id"], assignment["id"], self.term["id"], status, timestamp(), timestamp()))
        queue = next(q for q in self.get_overview()["operations"]["queues"] if q["key"] == "bubble_sheets")
        self.assertEqual(queue["failed"], 0)

    def test_custom_term_label_and_past_sittings_do_not_merge_into_upcoming(self):
        self.add_course(term="Other", term_other="Intensive", sections=[502])
        exams = self.app.extensions["exams"]
        catalog = exams.catalog()
        term = next(t for t in catalog["terms"] if t["term"] == "Other")
        section = next(s for s in catalog["sections"] if s["term_id"] == term["id"])
        assignment = self.app.extensions["documents"].create_assignment(term["definition_id"], "intensive-exam", "Intensive test")
        event = exams.create({"term_id": term["id"], "assignment_id": assignment["id"], "section_ids": [section["id"]]})
        for days, label in ((-1, "Past sitting"), (1, "Future sitting")):
            date = (datetime.now(ZoneInfo("America/Chicago")) + timedelta(days=days)).date().isoformat()
            event = exams.save_sitting(event["id"], {"version": event["version"], "label": label,
                                                    "date": date, "time": "14:00"})
        result = self.get_overview()["upcoming_exams"]
        self.assertEqual([(s["label"], s["term_label"]) for s in result], [("Future sitting", "Intensive 2026")])

    def test_utc_midnight_does_not_hide_the_campuses_evening_exam(self):
        exams = self.app.extensions["exams"]
        assignment = self.app.extensions["documents"].create_assignment(self.definition["id"], "evening-exam", "Evening test")
        event = exams.create({"term_id": self.term["id"], "assignment_id": assignment["id"], "section_ids": [self.section["id"]]})
        exams.save_sitting(event["id"], {"version": event["version"], "label": "Evening",
                                       "date": "2026-09-18", "time": "20:00"})
        with patch("workspace_overview.datetime", wraps=datetime) as clock:
            # 00:30 UTC is still September 18, 19:30 on campus (CDT).
            clock.now.return_value = datetime(2026, 9, 19, 0, 30, tzinfo=timezone.utc)
            upcoming = self.get_overview()["upcoming_exams"]
            self.assertEqual([(s["date"], s["time"]) for s in upcoming], [("2026-09-18", "20:00")])
            clock.now.return_value = datetime(2026, 9, 19, 5, 1, tzinfo=timezone.utc)
            self.assertEqual(self.get_overview()["upcoming_exams"], [])

    def test_queued_work_with_missing_or_stale_worker_needs_attention(self):
        self.submit()
        for heartbeat in (None, (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(), timestamp()):
            with self.subTest(heartbeat=heartbeat):
                if heartbeat:
                    with connect(self.inbox.path) as db:
                        db.execute("INSERT INTO inbox_worker VALUES(1,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at", (heartbeat,))
                summary = self.get_overview()
                reminders = [item for item in summary["attention"] if item["label"] == "Queued work is waiting for the worker"]
                if summary["operations"]["worker"]["state"] == "active":
                    self.assertEqual(reminders, [])
                else:
                    self.assertEqual([(r["count"], r["href"]) for r in reminders], [(1, "#overview?operations")])

    def test_running_rows_under_an_old_heartbeat_are_not_reported_as_progress(self):
        running, queued = self.submit(), self.submit(filename="second.txt", content=b"Second")
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET status='running' WHERE id=?", (running["id"],))
        stuck = "Work may be stuck: the worker stopped responding"
        for minutes, state in ((5, "busy"), (15, "stalled")):
            with self.subTest(minutes=minutes):
                with connect(self.inbox.path) as db:
                    db.execute("INSERT INTO inbox_worker VALUES(1,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at",
                               ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),))
                summary = self.get_overview()
                self.assertEqual(summary["operations"]["worker"]["state"], state)
                labels = {item["label"]: item["count"] for item in summary["attention"]}
                # A long turn may still be working; past the limit the running row is reported with the queued one, never as progress.
                self.assertEqual(labels.get(stuck), None if state == "busy" else 2)
                self.assertNotIn("Queued work is waiting for the worker", labels)
        self.assertEqual(self.inbox.get(queued["id"])["status"], "pending")


class OverviewStorageTests(OverviewTests):
    managed = True

    def test_missing_backup_drive_keeps_only_nvme_catalog_diagnostics_available(self):
        with patch.object(self.storage, "check_hdd", side_effect=StorageError("Backup drive unavailable")):
            core = self.client.get("/api/state?include=core")
            self.assertEqual(core.status_code, 200, core.json)
            summary = self.get_overview()
            self.assertEqual(summary["operations"]["storage"]["state"], "unavailable")
            for path in ("/api/state", "/api/state?include=core,rooms", "/api/inbox/jobs"):
                self.assertEqual(self.client.get(path).status_code, 503)
            self.assertEqual(self.client.post("/api/tools", json={"name": "Must not be saved"}, headers=HEADERS).status_code, 503)
            with patch.object(self.storage, "check_nvme", side_effect=StorageError("Working drive unavailable")):
                self.assertEqual(self.client.get("/api/state?include=core").status_code, 503)
                self.assertEqual(self.client.get("/api/workspace/overview").status_code, 503)

    def test_successful_backup_exposes_verified_timestamp_without_snapshot_paths(self):
        self.assertEqual(self.get_overview()["operations"]["storage"]["backup"]["status"], "not_run")
        self.storage.backup_all()
        state = self.get_overview()["operations"]["storage"]
        self.assertEqual(state["backup"]["status"], "complete")
        self.assertEqual(state["backup"]["verified_at"], state["backup"]["finished_at"])
        self.assertIsNotNone(state["backup"]["verified_at"])
        self.assertEqual(len(state["disks"]), 2)
        self.assertNotIn("catalog_snapshot", json.dumps(state))
        self.assertNotIn(self.directory.name, json.dumps(state))

    def test_failed_or_missing_drive_is_never_reported_as_verified_or_root_disk(self):
        with patch.object(self.storage, "status", return_value={"issues": [], "last_backup": {
            "status": "failed", "finished_at": timestamp(), "errors": ["Secret private path"]}}):
            state = storage_status(self.storage)
        self.assertIsNone(state["backup"]["verified_at"])
        self.assertEqual(state["backup"]["error_count"], 1)
        self.assertNotIn("Secret", json.dumps(state))
        with patch.object(self.storage, "check_hdd", side_effect=StorageError("Private path absent")), \
             patch("workspace_overview.shutil.disk_usage") as usage:
            state = storage_status(self.storage)
        usage.assert_not_called()
        self.assertEqual(state["state"], "unavailable")
        self.assertIsNone(state["backup"]["verified_at"])


class WorkerSchedulingTests(InboxTestCase):
    def processors(self, stack, executed, events, ready=True):
        owners = ((self.inbox, "process_one", "inbox"),
                  (self.inbox.documents, "build_one", "printing"),
                  (self.inbox.print_email_exports, "process_one", "print_mail"),
                  (self.inbox.response_cards.mail, "process_one", "card_mail"),
                  (self.inbox.student_chart_mail, "process_one", "chart_mail"),
                  (self.inbox.document_scans, "process_one", "scans"),
                  (self.inbox.document_scans.alias_ocr, "process_one", "alias_ocr"),
                  (self.inbox.document_scans.version_sort, "process_one", "version_sort"),
                  (self.inbox.bubble_sheets, "process_one", "bubble_sheets"))
        for owner, method, key in owners:
            def run(key=key):
                executed.append(key)
                events.append(key)
                return ready
            stack.enter_context(patch.object(owner, method, side_effect=run))
        def revoke(limit):
            self.assertEqual(limit, 1)
            events.append("revoke")
            return {"complete": int(ready)}
        stack.enter_context(patch.object(self.inbox.response_cards, "reconcile_invalidations", side_effect=revoke))

    def test_continuous_intake_and_revocations_do_not_starve_any_queue(self):
        executed, events = [], []
        with ExitStack() as stack:
            self.processors(stack, executed, events)
            for _ in range(20):
                self.assertTrue(self.inbox.work_once())
        self.assertEqual(Counter(executed), Counter({key: 2 for key in (
            "inbox", "printing", "print_mail", "card_mail", "chart_mail", "scans", "alias_ocr", "version_sort", "bubble_sheets")}))
        for i, event in enumerate(events):
            if event in ("card_mail", "chart_mail"):
                self.assertEqual(events[i - 1], "revoke")
        with connect(self.inbox.path) as db:
            self.assertIsNotNone(db.execute("SELECT heartbeat_at FROM inbox_worker WHERE id=1").fetchone())

    def test_empty_rotation_returns_false_and_checks_each_queue_once(self):
        executed, events = [], []
        with ExitStack() as stack:
            self.processors(stack, executed, events, ready=False)
            self.assertFalse(self.inbox.work_once())
        self.assertEqual(len(executed), 9)
        self.assertEqual(len(set(executed)), 9)

    def test_exception_advances_turn_and_revocation_failure_blocks_mail(self):
        with patch.object(self.inbox, "process_one", side_effect=RuntimeError("Intake unavailable")), \
             patch.object(self.inbox.documents, "build_one", return_value=True) as build:
            with self.assertRaises(RuntimeError):
                self.inbox.work_once()
            self.assertTrue(self.inbox.work_once())
            build.assert_called_once()
        self.inbox._work_queue_cursor = 4
        with patch.object(self.inbox.response_cards, "reconcile_invalidations", side_effect=RuntimeError("Revocation unavailable")), \
             patch.object(self.inbox.response_cards.mail, "process_one") as send:
            with self.assertRaises(RuntimeError):
                self.inbox.work_once()
            send.assert_not_called()
        self.assertEqual(self.inbox._work_queue_cursor, 5)

    def test_scan_turn_precedes_its_dependent_ocr_without_authorizing_commands(self):
        pending = []
        job = self.submit({"action": "scan.receive"}, filename="scan.pdf", content=b"%PDF-fixture", instructor_review_required=True)
        self.inbox._work_queue_cursor = 6
        def inspect():
            pending.append(job["message_id"])
            return True
        def recognize():
            self.assertEqual(pending.pop(), job["message_id"])
            return True
        with patch.object(self.inbox.document_scans, "process_one", side_effect=inspect), \
             patch.object(self.inbox.document_scans.alias_ocr, "process_one", side_effect=recognize):
            self.assertTrue(self.inbox.work_once())
            self.assertTrue(self.inbox.work_once())
        saved = self.inbox.get(job["id"])
        self.assertTrue(saved["instructor_review_required"])
        self.assertEqual(saved["attempts"], 0)

    def test_worker_lock_prevents_scheduler_or_mail_from_running_concurrently(self):
        self.inbox._work_queue_cursor = 4
        with self.inbox.worker_lock(), patch.object(self.inbox.response_cards.mail, "process_one") as send:
            with self.assertRaises(StorageError):
                self.inbox.work_once()
            send.assert_not_called()


class ManagedWorkerSchedulingTests(WorkerSchedulingTests):
    managed = True


def overview_of(case):
    if "workspace_overview" not in case.app.blueprints:
        register(case.app)
    response = case.client.get("/api/workspace/overview")
    case.assertEqual(response.status_code, 200, response.get_data(as_text=True))
    return response.json


def cards_by_key(summary):
    return {item["key"]: item for item in summary["attention"]}


class OverviewSeatAndCardTests(unittest.TestCase):
    """Response Card and seat-update work is counted per card, from retained records, with no recipient on Home."""
    for _name in ("setUp", "drain", "current", "student", "move", "send_updates", "mail_rows", "import_dr", "create", "confirm", "recipient"):
        locals()[_name] = getattr(seat_fixture.SeatingUpdateTests, _name)
    del _name

    def test_seat_updates_and_card_delivery_are_counted_per_card_without_recipients(self):
        summary = overview_of(self)
        self.assertFalse([key for key in cards_by_key(summary) if key.startswith(("cards_", "seat_updates"))])
        self.move()
        chart = self.current()
        summary = overview_of(self)
        seat = cards_by_key(summary)["seat_updates:" + chart["id"]]
        self.assertEqual((seat["count"], seat["href"]), (chart["seat_updates"]["pending"], "#seating/" + chart["id"]))
        self.assertIn("HECC 105", seat["label"])
        self.assertNotIn("Bob", json.dumps(summary))
        self.assertNotIn("@", json.dumps(summary["attention"]))
        self.send_updates()
        self.drain()
        self.assertNotIn("seat_updates:" + chart["id"], cards_by_key(overview_of(self)))

        # An unopened card whose latest invitation is unconfirmed; answered or replaced cards need no delivery check.
        with connect(self.path) as db:
            row = db.execute("""SELECT m.id,m.card_id,r.issue_id FROM response_card_mail m JOIN response_card_recipients r ON r.id=m.card_id
                WHERE m.kind='invitation' AND m.status='accepted' AND r.status='issued'
                AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id) ORDER BY m.rowid LIMIT 1""").fetchone()
            db.execute("UPDATE response_card_mail SET status='unconfirmed' WHERE id=?", (row["id"],))
        unconfirmed = cards_by_key(overview_of(self))["cards_unconfirmed"]
        self.assertEqual((unconfirmed["count"], unconfirmed["href"]), (1, "#response-cards/issue/" + row["issue_id"] + "?delivery=problem"))
        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET status='opened' WHERE id=?", (row["card_id"],))
        self.assertNotIn("cards_unconfirmed", cards_by_key(overview_of(self)))

        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET status='needs_review' WHERE id=?", (row["card_id"],))
            db.execute("UPDATE seating_charts SET exam_date='2999-01-05' WHERE id=?", (chart["id"],))  # the exam is still ahead
        flagged = cards_by_key(overview_of(self))["cards_flagged"]
        self.assertEqual((flagged["count"], flagged["href"]), (1, "#response-cards/issue/" + row["issue_id"] + "?status=needs_review"))
        # RESPONSE-CARDS "NO FLAGS AFTER THE EXAM" (requirement, 2026-10-23): from midnight after the exam day a flagged answer is no
        # longer Home's or the Response Cards attention strip's; the card itself is unchanged.
        cards = self.app.extensions["response_cards"]
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-01-05' WHERE id=?", (chart["id"],))
        self.assertNotIn("cards_flagged", cards_by_key(overview_of(self)))
        self.assertFalse(any(item["counts"]["needs_review"] for item in cards.attention()["items"]))
        self.assertEqual(cards.get_issue(row["issue_id"])["counts"]["needs_review"], 1)
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2999-01-05' WHERE id=?", (chart["id"],))
        self.assertEqual(cards_by_key(overview_of(self))["cards_flagged"]["count"], 1)
        self.assertEqual(sum(item["counts"]["needs_review"] for item in cards.attention()["items"]), 1)


class OverviewGradingTests(unittest.TestCase):
    """The Grading badge's light counts agree with the grading workflow for proposals and failures."""
    for _name in ("setUp", "configure_rubric", "queue", "finish", "approve", "document_version", "ready_batch", "reviewed_source"):
        locals()[_name] = getattr(frq_fixture.FrqWorkflowTests, _name)
    del _name

    def workflow(self):
        return next(p for p in self.frq.overview(include_health=False)["assignments"] if p["id"] == self.plan["id"])["workflow"]

    def test_waiting_review_count_matches_the_workflow_and_clears_on_approval(self):
        self.assertFalse([k for k in cards_by_key(overview_of(self)) if k.startswith("grading:")])
        # Home scopes the rows it derives from Grading's overview (every term's plans) to these, the cards' own scope.
        self.assertEqual(overview_of(self)["current_grading_plans"], [self.plan["id"]])
        job, _ = self.queue()
        response = self.finish(job)
        waiting = cards_by_key(overview_of(self))[f"grading:{self.plan['id']}:review"]
        self.assertEqual(waiting["count"], self.workflow()["review_responses"])
        self.assertEqual(waiting["count"], 1)
        self.assertEqual(waiting["href"], f"#grading/{self.plan['id']}?tab=review")
        self.assertEqual(waiting["label"], "Review grades · Exam 1")
        self.approve(response)
        self.assertEqual(self.workflow()["review_responses"], 0)
        self.assertNotIn(f"grading:{self.plan['id']}:review", cards_by_key(overview_of(self)))

    def test_the_grading_overview_says_whether_grading_starts_by_itself(self):
        """Additive (journey-5): each plan of GET /api/grading carries the plan read's auto_grade, so Home can say grading stopped."""
        listed = next(p for p in self.frq.overview(include_health=False)["assignments"] if p["id"] == self.plan["id"])
        self.assertEqual(listed["auto_grade"], self.frq.assignment(self.plan["id"])["auto_grade"])
        self.assertEqual(set(listed["auto_grade"]), {"enabled", "paused", "not_started", "settle_seconds"})
        navigation = next(p for p in self.frq.overview(navigation=True)["assignments"] if p["id"] == self.plan["id"])
        self.assertNotIn("auto_grade", navigation)  # The navigation read stays as it was.

    def add_check(self, status, input_sha256=None, resolves=None):
        """A rubric check as the worker leaves it (a rerun inserts a new row), copied from the fixture's confirmed one; `resolves`
        makes it a resolution of that check."""
        with connect(self.path) as db:
            row = dict(db.execute("SELECT * FROM frq_rubric_quality_checks WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (self.plan["id"],)).fetchone())
            row.update(id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), status=status, input_sha256=input_sha256 or uuid.uuid4().hex * 2,
                       error="Synthetic failure" if status == "failed" else "", created_at=timestamp(), updated_at=timestamp(), cancelled=0)
            if resolves:
                row["input_json"] = json.dumps({"resolves": {"check_id": resolves, "concerns": []}})
            db.execute(f"INSERT INTO frq_rubric_quality_checks({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
        return row

    def quality_state(self):
        """What the Grading page says of the plan's rubric check: the authority the card follows."""
        return self.frq.rubric_quality(self.plan["id"])["state"]

    def rerun(self):
        """The rubric check asked again and finished, as the page asks it: the plan's confirmation pointer is unset, and the check
        read exactly today's rubrics."""
        with connect(self.path) as db:
            db.execute("UPDATE frq_rubric_quality_options SET review_id=NULL WHERE assignment_id=?", (self.plan["id"],))
        return self.add_check("complete", self.frq.rubric_quality(self.plan["id"])["input_sha256"])

    def test_a_finished_rubric_check_waits_for_review_until_confirmed_or_made_stale(self):
        """grading-setup-5: a rubric check that finished while the instructor was elsewhere raised no card, badge or notice. The card
        follows the Grading page's own state, compared at every step."""
        plan = self.plan["id"]
        key = f"grading:{plan}:finalization"
        waits = lambda: key in cards_by_key(overview_of(self))
        self.assertEqual((self.quality_state(), waits()), ("confirmed", False), "the fixture's check is confirmed")
        # A check of the input the standing confirmation covers needs nothing; nor does one of an input that is no longer today's.
        with connect(self.path) as db:
            covered = db.execute("SELECT input_sha256 FROM frq_rubric_quality_reviews WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (plan,)).fetchone()[0]
        self.add_check("complete", covered)
        self.assertEqual((self.quality_state(), waits()), ("confirmed", False))
        self.add_check("complete")
        self.assertEqual((self.quality_state(), waits()), ("confirmed", False))
        check = self.rerun()
        self.assertEqual((self.quality_state(), waits()), ("needs_review", True))
        card = cards_by_key(overview_of(self))[key]
        self.assertEqual((card["count"], card["href"], card["kind"], card["verb"]), (1, f"#grading/{plan}?tab=answer-key&open=quality", "review", "Review"))
        # Review fix (2026-10-02): saving the answer key makes a new question revision, but the check never reads the key, so it
        # still waits; the card used to leave on any newer question revision.
        q = self.frq.assignment(plan)["questions"][0]
        self.frq.save_answer_key(plan, {"version": self.frq.assignment(plan)["version"], "questions": [{"question_id": self.qid, "version": q["version"],
                                                                                                        "answer_key": [{"label": "x", "accept": ["x=7"]}]}]})
        self.assertNotEqual(self.frq.assignment(plan)["questions"][0]["question_revision_id"], q["question_revision_id"])
        self.assertEqual((self.quality_state(), waits()), ("needs_review", True))
        # A resolution that failed leaves the review it answered waiting, as on the Grading page.
        self.add_check("failed", resolves=check["id"])
        self.assertEqual((self.quality_state(), waits()), ("needs_review", True))
        finalize_rubrics(self.frq, plan)
        self.assertEqual((self.quality_state(), waits()), ("confirmed", False), "confirmed")
        # A line comment is read by the check: one written after it makes it stale (it must run again), with no newer revision.
        self.rerun()
        self.assertTrue(waits())
        q = self.frq.assignment(plan)["questions"][0]
        self.frq.edit_rubric(plan, self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"],
                                              "comments": {"R1": "Count a sign slip once."}})
        self.assertEqual((self.quality_state(), waits()), ("needed", False))
        self.rerun()
        self.assertTrue(waits())
        q = self.frq.assignment(plan)["questions"][0]
        self.frq.import_rubric(plan, self.qid, {"version": q["version"], "source_text": "-1 Sign error",
            "rules": {"items": [{"id": "R1", "description": "Sign error", "points": -1, "original_source_text": "-1 Sign error"}]}})
        self.assertNotIn(self.quality_state(), ("needs_review", "confirmed"))
        self.assertFalse(waits(), "a later rubric revision makes the check stale: it must run again")

    def add_generation(self, status, *, key=None, version=""):
        """A rubric draft of a template version, or (`key`: a question id) an answer-key reading of one question, as the worker
        leaves it; both live in frq_rubric_generations, with the question's printed version and template."""
        with connect(self.path) as db:
            template = db.execute("SELECT id FROM assignment_templates WHERE assignment_id=?", (self.plan["source_assignment_id"],)).fetchone()
            if template:
                template = template[0]
            else:
                template = str(uuid.uuid4())
                db.execute("INSERT INTO assignment_templates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (template, self.plan["source_assignment_id"], "Blank",
                    str(uuid.uuid4()), "a" * 64, 2, "pdf", "[]", f"grading-templates/{template}/manifest.json", "b" * 64, timestamp(), None))
            row = {"id": str(uuid.uuid4()), "assignment_id": self.plan["id"], "request_id": str(uuid.uuid4()), "request_sha256": "c" * 64,
                   "document_id": self.version_id, "exam_version": version, "template_id": template,
                   "input_json": json.dumps({"kind": "key", "question_id": key} if key else {"exam_version": version}),
                   "profile_json": json.dumps({"endpoint": {"name": "Scripted"}}), "status": status,
                   "error": "Synthetic failure" if status == "failed" else "", "created_at": timestamp(), "updated_at": timestamp()}
            db.execute(f"INSERT INTO frq_rubric_generations({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
        return row

    def test_rubric_drafts_and_answer_key_readings_are_superseded_apart(self):
        """Review fix (2026-10-02): drafts and answer-key readings share a table, and a later reading of ANOTHER question (or any
        reading of a version) discounted a failure that Grading still shows."""
        plan = self.plan["id"]
        drafts = lambda summary: next(q for q in summary["operations"]["queues"] if q["key"] == "frq_rubric_drafts")
        self.add_generation("failed", key="q-one")
        self.add_generation("complete", key="q-two")
        summary = overview_of(self)
        self.assertEqual(drafts(summary)["failed"], 1)
        card = cards_by_key(summary)[f"queue:frq_rubric_drafts:failed:{plan}:key"]
        # Its own name, on the key's step, where Grading shows a failed reading; the queue's row opens that step too.
        self.assertEqual((card["label"], card["href"], card["count"]), ("Answer key readings: failed · Exam 1", f"#grading/{plan}?tab=answer-key", 1))
        self.assertEqual(drafts(summary)["href"], f"#grading/{plan}?tab=answer-key")
        self.add_generation("complete", key="q-one")
        self.assertEqual(drafts(overview_of(self))["failed"], 0, "read again")
        # A failed draft counts until a later draft of its version replaces it; a reading of that version does not.
        self.add_generation("failed", version="A")
        self.add_generation("complete", key="q-one", version="A")
        summary = overview_of(self)
        self.assertEqual(drafts(summary)["failed"], 1)
        card = cards_by_key(summary)[f"queue:frq_rubric_drafts:failed:{plan}"]
        self.assertEqual((card["label"], card["href"]), ("Rubric drafts: failed · Exam 1", f"#grading/{plan}?tab=rubrics&focus=templates"))
        self.add_generation("complete", version="A")
        summary = overview_of(self)
        self.assertEqual(drafts(summary)["failed"], 0)
        self.assertEqual(drafts(summary)["href"], "#grading?tab=rubrics", "nothing to open on one plan: the queue's own link")
        self.assertFalse([key for key in cards_by_key(summary) if key.startswith("queue:frq_rubric_drafts")])

    def test_a_rubric_check_that_failed_then_ran_again_is_no_failure(self):
        plan = self.plan["id"]
        self.add_check("failed")
        summary = overview_of(self)
        queue = next(q for q in summary["operations"]["queues"] if q["key"] == "frq_rubric_quality")
        self.assertEqual((queue["label"], queue["failed"]), ("Rubric checks", 1))
        card = cards_by_key(summary)[f"queue:frq_rubric_quality:failed:{plan}"]
        # The plan's own card on the step that shows the check; no queue-wide card counts it again.
        self.assertEqual((card["count"], card["href"], card["label"]), (1, f"#grading/{plan}?tab=answer-key&open=quality", "Rubric checks: failed · Exam 1"))
        self.assertNotIn("queue:frq_rubric_quality:failed", cards_by_key(summary))
        self.add_check("complete")
        summary = overview_of(self)
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "frq_rubric_quality")["failed"], 0)
        self.assertFalse([key for key in cards_by_key(summary) if key.startswith("queue:frq_rubric_quality")])

    def test_nothing_failed_keeps_the_response_of_the_earlier_queue_rules(self):
        job, _ = self.queue()
        self.finish(job)
        current = without_time(overview_of(self))
        self.assertTrue(any(q["key"] == "frq_grading" for q in current["operations"]["queues"]))
        with patch("workspace_overview.QUEUE_SPECS", EARLIER_SPECS):
            self.assertEqual(without_time(overview_of(self)), current)


class OverviewGradingFailureTests(cloud_fixture.CloudCase):
    def test_failed_count_matches_the_workflow(self):
        self.host.script = lambda body, host: ("status", 402)
        self.queue()
        self.run_all()
        workflow = next(p for p in self.frq.overview(include_health=False)["assignments"] if p["id"] == self.plan["id"])["workflow"]
        summary = overview_of(self)
        failed = cards_by_key(summary)[f"grading:{self.plan['id']}:failed"]
        self.assertEqual(failed["count"], workflow["failed"])
        self.assertEqual(failed["count"], 1)
        self.assertEqual(failed["kind"], "error")
        # Named and linked as the assignment's needs-you item names it (2026-10-18): Grade's Failed to grade.
        self.assertEqual((failed["label"], failed["href"], failed["verb"]),
                         ("Inspect failed grading · Exam 1", f"#grading/{self.plan['id']}?tab=review&open=failed", "Inspect"))
        # One failure, one card (review fix, 2026-10-02): the queue's card counted it again on Home and in the Grading badge.
        self.assertEqual([key for key in cards_by_key(summary) if "frq_grading" in key or key.startswith("grading:")], [failed["key"]])
        queue = next(q for q in summary["operations"]["queues"] if q["key"] == "frq_grading")
        self.assertEqual((queue["label"], queue["failed"]), ("Grading batches", 1))

    def workflow_failed(self):
        return next(p for p in self.frq.overview(include_health=False)["assignments"] if p["id"] == self.plan["id"])["workflow"]["failed"]

    def test_a_failure_under_an_earlier_rubric_is_work_to_grade_again(self):
        """Review fix (2026-09-26): after a rubric edit the Grading page files the failure as a regrade; the badge followed it."""
        self.host.script = lambda body, host: ("status", 402)
        self.queue()
        self.run_all()
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        item = q["rubric"]["rules"]["items"][0]["id"]
        self.frq.edit_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"],
                                                     "lines": [{"item_id": item, "description": "Edited wording"}]})
        self.assertEqual(self.workflow_failed(), 0)
        self.assertNotIn(f"grading:{self.plan['id']}:failed", cards_by_key(overview_of(self)))
        self.assertNotIn("queue:frq_grading:failed", cards_by_key(overview_of(self)))

    def test_a_failure_in_a_cancelled_batch_is_new_work(self):
        self.host.script = lambda body, host: ("status", 402)
        job = self.queue()
        self.run_all()
        self.frq.cancel_job(job["id"] if isinstance(job, dict) else job[0]["id"])
        self.assertEqual(self.workflow_failed(), 0)
        summary = overview_of(self)
        self.assertNotIn(f"grading:{self.plan['id']}:failed", cards_by_key(summary))
        self.assertNotIn("queue:frq_grading:failed", cards_by_key(summary))
        # The cancelled batch's failure is history in the health table too (bugs-backend-2): Retry refuses it.
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "frq_grading")["failed"], 0)


class OverviewAnswerAreaTests(unittest.TestCase):
    """grading-setup-5: suggested answer areas that finished reading wait for the instructor's look, counted per question."""
    for _name in ("setUp", "upload", "question", "finish_check", "confirmation", "configure_rubric", "document_version", "ready_batch",
                  "reviewed_source", "queue"):
        locals()[_name] = getattr(locations_fixture.LocationWorkflow, _name)
    del _name
    managed = False

    def test_answer_areas_ready_to_confirm_are_counted_until_confirmed(self):
        key = f"grading:{self.plan['id']}:areas"
        self.assertNotIn(key, cards_by_key(overview_of(self)))
        self.upload()
        self.assertNotIn(key, cards_by_key(overview_of(self)), "nothing to look at while the template is read")
        self.assertEqual(self.finish_check()["status"], "needs_confirmation")
        card = cards_by_key(overview_of(self))[key]
        self.assertEqual((card["count"], card["href"], card["verb"]), (1, f"#grading/{self.plan['id']}?tab=locations", "Check"))
        # The assignment's own needs-you words (2026-10-18), as its Prepare tab and the index name it.
        self.assertEqual(card["label"], "Check answer areas and templates · Exam 1")
        self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation())
        self.assertNotIn(key, cards_by_key(overview_of(self)))

    def test_a_failed_check_whose_areas_were_confirmed_is_no_failure(self):
        """2026-10-26: Exam 2's 16 failed template readings stayed "Answer area checks: failed" after every area was confirmed."""
        key = f"queue:frq_locations:failed:{self.plan['id']}"
        self.upload()
        self.reader.fail_reading = True
        self.assertEqual(self.finish_check()["status"], "failed")
        summary = overview_of(self)
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "frq_locations")["failed"], 1)
        self.assertEqual(cards_by_key(summary)[key]["count"], 1)
        self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation(notes="Areas checked by hand.", manual_review=True))
        self.assertEqual(self.question()["location_check"]["status"], "confirmed")
        summary = overview_of(self)
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "frq_locations")["failed"], 0)
        self.assertFalse([card for card in cards_by_key(summary) if card.startswith("queue:frq_locations")])


class OverviewArchivedScansTests(archive_fixture.ArchivedAfterCertification):
    """journey-3: Home's 'finished · History' line pointed at History while the Inbox lists certified scans under Archived."""

    def test_finished_scans_of_a_certified_assessment_are_counted_as_archived(self):
        before = overview_of(self)
        self.assertGreater(before["finished_recent"], 0)
        self.assertEqual(before["finished_recent_archived"], 0)
        self.certify()
        after = overview_of(self)
        self.assertEqual(after["finished_recent"], before["finished_recent"])
        history = self.client.get("/api/inbox/jobs?status=history").get_json()
        archived = self.client.get("/api/inbox/jobs?status=archived").get_json()
        scans = lambda listing: sum(job["action"] == "scan.receive" for job in listing["jobs"])
        self.assertEqual(after["finished_recent_archived"], scans(archived))
        self.assertEqual(after["finished_recent"] - after["finished_recent_archived"], scans(history))


for _name in [n for n in vars(archive_fixture.ArchivedAfterCertification) if n.startswith("test_") and n not in vars(OverviewArchivedScansTests)]:
    setattr(OverviewArchivedScansTests, _name, None)  # ArchivedAfterCertification's own tests run in test_certified_archive
del _name


class OverviewPrintFailureTests(unittest.TestCase):
    """journey-6 (4): one failed print job opens itself; several open the print jobs that need attention."""
    for _name in ("setUp", "add_course", "version_for", "batch"):
        locals()[_name] = getattr(documents_fixture.DocumentTests, _name)
    del _name
    managed = False

    def test_one_failed_print_job_links_to_itself(self):
        first, second = self.batch(), self.batch()
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET status='failed',error='Synthetic failure' WHERE id=?", (first["id"],))
        card = cards_by_key(overview_of(self))["queue:printing:failed"]
        self.assertEqual((card["count"], card["href"]), (1, f"#documents?batch={first['id']}"))
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET status='failed',error='Synthetic failure' WHERE id=?", (second["id"],))
        summary = overview_of(self)
        card = cards_by_key(summary)["queue:printing:failed"]
        # ASSIGNMENT-WORKSPACE: a bare #documents opens Assignments; several failures open the print jobs needing attention.
        self.assertEqual((card["count"], card["href"]), (2, "#documents?tab=batches&status=attention"))
        self.assertEqual(next(q for q in summary["operations"]["queues"] if q["key"] == "printing")["href"], "#documents?tab=batches")
