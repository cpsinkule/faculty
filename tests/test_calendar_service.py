"""Custom calendar ownership, wall-clock dates, concurrency, and durable receipts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from werkzeug.exceptions import Conflict, NotFound

from app import create_app
from calendar_service import CalendarService
from rosters import connect


HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


class CalendarServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()
        self.rosters = self.app.extensions["rosters"]
        self.service = CalendarService(self.rosters)
        for code, term, other in (("MATH 140", "Fall", ""), ("MATH 140", "Other", "May intensive"), ("PHYS 201", "Fall", "")):
            response = self.client.post("/api/courses", json={"code": code, "name": "Synthetic calendar course", "term": term,
                "term_other": other, "year": 2026, "sections": [501, 502], "multiple_sections": True}, headers=HEADERS)
            self.assertEqual(response.status_code, 201, response.json)
        catalog = self.service.catalog()
        self.definition = next(row["id"] for row in catalog["courses"] if row["code"] == "MATH 140")
        self.other_definition = next(row["id"] for row in catalog["courses"] if row["code"] == "PHYS 201")
        self.term = next(row["id"] for row in catalog["terms"] if row["definition_id"] == self.definition and row["term_label"] == "Fall 2026")
        self.other_term = next(row["id"] for row in catalog["terms"] if row["definition_id"] == self.definition and row["term_label"] == "May intensive 2026")
        self.section = next(row["id"] for row in catalog["sections"] if row["term_id"] == self.term and row["number"] == 501)
        self.other_section = next(row["id"] for row in catalog["sections"] if row["term_id"] == self.other_term and row["number"] == 501)

    def payload(self, **values):
        return {"title": "Office hours", "date": "2026-09-19", "request_id": str(uuid.uuid4()), **values}

    def create(self, **values):
        return self.service.create(self.payload(**values))

    def test_catalog_uses_permanent_hierarchy_and_custom_term_label(self):
        catalog = self.service.catalog()
        self.assertEqual(len(catalog["courses"]), 2)
        self.assertEqual(len(catalog["terms"]), 3)
        self.assertEqual(len(catalog["sections"]), 6)
        self.assertIn({"id": self.other_term, "definition_id": self.definition, "term_label": "May intensive 2026"}, catalog["terms"])
        groups = self.client.get("/api/state").json["course_groups"]
        self.assertNotIn(self.term, [row["id"] for row in groups])

    def test_explicit_owners_and_display_context(self):
        cases = (("workspace", {}), ("course", {"definition_id": self.definition}),
                 ("term", {"definition_id": self.definition, "term_id": self.term}),
                 ("section", {"definition_id": self.definition, "term_id": self.term, "section_id": self.section}))
        for scope, owners in cases:
            with self.subTest(scope=scope):
                result = self.create(scope=scope, **owners)
                self.assertEqual(result["scope"], scope)
                self.assertEqual(result["source"], "custom")
                self.assertTrue(result["editable"])
                self.assertEqual(str(uuid.UUID(result["id"])), result["id"])
                self.assertEqual(result["version"], 1)
                self.assertEqual(result["end_date"], result["date"])
                self.assertTrue(result["all_day"])
                self.assertEqual(result["time"], "")
                self.assertNotIn("request_id", result)
                self.assertNotIn("request_sha256", result)
                self.assertEqual(result["code"], "MATH 140" if scope != "workspace" else None)
                self.assertEqual(result["term_label"], "Fall 2026" if scope in ("term", "section") else "")
                self.assertEqual(result["section_number"], 501 if scope == "section" else None)
                for key, value in owners.items():
                    self.assertEqual(result[key], value)

    def test_wall_clock_time_leap_dates_and_overnight_event(self):
        event = self.create(date="2028-02-29", end_date="2028-03-01", all_day=False, time="23:45", end_time="00:15",
                            category="deadline", location="HECC 105", notes="Bring a draft.\nQuestions welcome.")
        self.assertEqual((event["date"], event["end_date"], event["time"], event["end_time"]),
                         ("2028-02-29", "2028-03-01", "23:45", "00:15"))
        self.assertEqual(event["notes"], "Bring a draft.\nQuestions welcome.")
        same_minute = self.create(all_day=False, time="10:30", end_time="10:30")
        self.assertFalse(same_minute["all_day"])

    def test_invalid_dates_times_and_text_are_atomic(self):
        cases = ({"title": " "}, {"title": "t" * 161}, {"notes": "n" * 4001}, {"location": "x" * 241},
                 {"title": None}, {"notes": 5}, {"notes": "bad\x00text"}, {"date": "2026-2-03"},
                 {"date": "2026-02-29"}, {"date": "2026-09-19T00:00:00Z"}, {"date": "20260919"},
                 {"end_date": "2026-09-18"}, {"end_date": 0}, {"all_day": "true"}, {"all_day": 1},
                 {"all_day": False}, {"time": "10:00"}, {"all_day": False, "time": "24:00"},
                 {"all_day": False, "time": "9:00"}, {"all_day": False, "time": "10:60"},
                 {"all_day": False, "time": "10:00Z"}, {"all_day": False, "time": None},
                 {"all_day": False, "time": "10:00", "end_time": "09:59"}, {"category": "exam"},
                 {"deleted_at": "2026-01-01"})
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.create(**values)
        self.assertEqual(self.service.list("2026-01-01", "2026-12-31"), [])
        self.assertEqual(len(self.create(title="t" * 160, notes="n" * 4000, location="x" * 240)["notes"]), 4000)

    def test_invalid_scope_and_parent_mismatches_are_rejected(self):
        cases = ({"scope": "section"}, {"scope": "saved_entry"}, {"scope": []},
                 {"scope": "workspace", "definition_id": self.definition},
                 {"scope": "course", "definition_id": self.definition, "term_id": self.term},
                 {"scope": "course", "definition_id": "missing"},
                 {"scope": "term", "definition_id": self.other_definition, "term_id": self.term},
                 {"scope": "term", "definition_id": self.definition, "term_id": "missing"},
                 {"scope": "section", "definition_id": self.definition, "term_id": self.term, "section_id": self.other_section},
                 {"scope": "section", "definition_id": self.definition, "term_id": self.term, "section_id": "501"})
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.create(**values)
        self.assertEqual(self.service.list("2026-09-01", "2026-09-30"), [])

    def test_range_overlap_is_inclusive_and_bounded(self):
        spanning = self.create(title="Reading week", date="2026-08-29", end_date="2026-10-02")
        first = self.create(title="First day", date="2026-09-01")
        last = self.create(title="Last day", date="2026-09-30")
        self.create(date="2026-08-28")
        self.create(date="2026-10-03")
        self.assertEqual({row["id"] for row in self.service.list("2026-09-01", "2026-09-30")}, {spanning["id"], first["id"], last["id"]})
        self.assertEqual({row["id"] for row in self.service.list("2026-09-30", "2026-09-30")}, {spanning["id"], last["id"]})
        self.service.list("2028-01-01", "2028-12-31")  # A full leap year is valid.
        for start, end in (("2026-09-30", "2026-09-01"), ("2028-01-01", "2029-01-01"), ("bad", "2026-09-30")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.service.list(start, end)

    def test_create_request_is_content_bound_and_survives_restart_and_edits(self):
        payload = self.payload()
        first = self.service.create(payload)
        self.assertEqual(self.service.create(payload), first)
        with self.assertRaises(Conflict):
            self.service.create({**payload, "notes": "Different intent"})
        edited = self.service.update(first["id"], {"version": 1, "title": "Revised office hours"})
        restarted = CalendarService(self.rosters)
        self.assertEqual(restarted.create(payload), edited)
        self.assertEqual(len(restarted.list("2026-09-01", "2026-09-30")), 1)
        self.assertEqual(len({first["id"], self.create()["id"]}), 2)
        for request_id in (None, "", "abc", "123456789", uuid.uuid4().hex, 12):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                self.create(request_id=request_id)

    def test_concurrent_create_replays_one_event_and_conflicting_edits_do_not_overwrite(self):
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.service.create(payload), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        identifier = results[0]["id"]
        def update(title):
            try:
                return self.service.update(identifier, {"version": 1, "title": title})
            except Conflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            updates = list(executor.map(update, ("First edit", "Second edit")))
        self.assertEqual(sum(row is not None for row in updates), 1)
        self.assertEqual(self.service.get(identifier)["version"], 2)

    def test_versioned_partial_edits_and_deliberate_scope_change(self):
        event = self.create(scope="section", definition_id=self.definition, term_id=self.term, section_id=self.section)
        changed = self.service.update(event["id"], {"version": 1, "notes": "Changed note"})
        self.assertEqual(changed["title"], event["title"])
        self.assertEqual(changed["section_id"], self.section)
        self.assertEqual(changed["version"], 2)
        with self.assertRaises(Conflict):
            self.service.update(event["id"], {"version": 1, "title": "Stale"})
        with self.assertRaises(ValueError):
            self.service.update(event["id"], {"version": 2, "scope": "workspace"})
        with self.assertRaises(ValueError):
            self.service.update(event["id"], {"version": 2, "term_id": self.other_term})
        moved = self.service.update(event["id"], {"version": 2, "scope": "workspace", "definition_id": None, "term_id": None, "section_id": None})
        self.assertEqual(moved["version"], 3)
        self.assertEqual(moved["scope"], "workspace")
        self.assertIsNone(moved["definition_id"])
        for version in (None, True, 0, -1, "3", 3.0):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.service.update(event["id"], {"version": version, "notes": "bad"})

    def test_soft_delete_requires_latest_version_and_never_resurrects_a_receipt(self):
        payload = self.payload()
        event = self.service.create(payload)
        updated = self.service.update(event["id"], {"version": 1, "notes": "Retained note"})
        with self.assertRaises(Conflict):
            self.service.delete(event["id"], {"version": 1})
        with self.assertRaises(ValueError):
            self.service.delete(event["id"], {"version": True})
        deleted = self.service.delete(event["id"], {"version": updated["version"]})
        self.assertEqual(deleted["version"], 3)
        self.assertTrue(deleted["deleted_at"])
        self.assertEqual(self.service.list("2026-09-01", "2026-09-30"), [])
        with self.assertRaises(NotFound):
            self.service.get(event["id"])
        with self.assertRaises(NotFound):
            self.service.update(event["id"], {"version": 3, "title": "Resurrect"})
        with self.assertRaises(NotFound):
            self.service.delete(event["id"], {"version": 3})
        with self.assertRaises(Conflict):
            self.service.create(payload)
        with connect(self.service.path) as db:
            row = db.execute("SELECT * FROM calendar_events WHERE id=?", (event["id"],)).fetchone()
            self.assertEqual(row["notes"], "Retained note")
            self.assertEqual(row["request_id"], payload["request_id"])

    def test_deleted_terms_are_not_new_owners_but_existing_events_remain_readable(self):
        owners = {"scope": "section", "definition_id": self.definition, "term_id": self.term, "section_id": self.section}
        event = self.create(**owners)
        with self.rosters.lock(), connect(self.service.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE course_terms SET deleted_at='2026-09-20' WHERE id=?", (self.term,))
        self.assertNotIn(self.term, [row["id"] for row in self.service.catalog()["terms"]])
        self.assertNotIn(self.section, [row["id"] for row in self.service.catalog()["sections"]])
        with self.assertRaises(ValueError):
            self.create(**owners)
        with self.assertRaises(ValueError):
            self.service.update(event["id"], {"version": 1, "title": "Cannot assign deleted term"})
        self.assertEqual(self.service.get(event["id"])["section_id"], self.section)
        self.assertEqual(len(self.service.list("2026-09-01", "2026-09-30")), 1)
        self.assertTrue(self.service.delete(event["id"], {"version": 1})["deleted_at"])

    def test_owner_code_changes_keep_the_original_permanent_reference(self):
        event = self.create(scope="course", definition_id=self.definition)
        with self.rosters.lock(), connect(self.service.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE course_definitions SET code='MATH 0140' WHERE id=?", (self.definition,))
        retained = self.service.get(event["id"])
        self.assertEqual(retained["definition_id"], self.definition)
        self.assertEqual(retained["code"], "MATH 0140")

    def test_migration_restart_and_failure_are_atomic(self):
        event = self.create()
        CalendarService(self.rosters)
        with connect(self.service.path) as db:
            ledger = db.execute("SELECT * FROM schema_migrations WHERE namespace='calendar'").fetchall()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["adoption"], "applied")
        def fail(db, *args, **kwargs):
            db.execute("CREATE TABLE calendar_failed_migration (id TEXT)")
            raise RuntimeError("Interrupted schema change")
        with patch("calendar_service.apply_sql", side_effect=fail), self.assertRaises(RuntimeError):
            CalendarService(self.rosters)
        with connect(self.service.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='calendar_failed_migration'").fetchone())
            self.assertEqual(db.execute("SELECT count(*) FROM schema_migrations WHERE namespace='calendar'").fetchone()[0], 1)
        self.assertEqual(CalendarService(self.rosters).get(event["id"]), event)

    def test_database_guards_hierarchy_and_lifecycle_lock_wraps_every_write(self):
        event = self.create(scope="section", definition_id=self.definition, term_id=self.term, section_id=self.section)
        with connect(self.service.path) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE calendar_events SET term_id=? WHERE id=?", (self.other_term, event["id"]))
        held = False
        original_lock, original_connect = self.rosters.lock, connect
        @contextmanager
        def tracked_lock():
            nonlocal held
            with original_lock():
                held = True
                try:
                    yield
                finally:
                    held = False
        @contextmanager
        def guarded_connect(path):
            self.assertTrue(held, "Calendar database opened before the roster lifecycle lock")
            with original_connect(path) as db:
                yield db
        with patch.object(self.rosters, "lock", tracked_lock), patch("calendar_service.connect", guarded_connect):
            restarted = CalendarService(self.rosters)
            created = restarted.create(self.payload())
            updated = restarted.update(created["id"], {"version": 1, "title": "Updated"})
            restarted.delete(created["id"], {"version": updated["version"]})


if __name__ == "__main__":
    unittest.main()
