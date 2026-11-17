"""Calendar source projection, wall-clock dates, privacy and guarded custom writes."""
from contextlib import ExitStack
from datetime import datetime, timezone
import io
import json
from urllib.parse import parse_qs
import tempfile
import unittest
from unittest.mock import patch
import uuid

from app import create_app
from calendar_service import CalendarService
from course_storage import StorageError
from rosters import connect
from test_dr_exams import booking, workbook
from test_rosters import COLUMNS, csv_bytes
from test_rooms import chart_bytes
from test_storage import HEADERS, temporary_storage
from workspace_calendar import register


class CalendarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = create_app(temporary.name)
        self.app.testing = True
        self.rosters = self.app.extensions["rosters"]
        self.service = self.app.extensions.get("calendar") or CalendarService(self.rosters)
        if "workspace_calendar" not in self.app.blueprints:
            register(self.app, self.service)
        self.client = self.app.test_client()
        response = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
            "sections": [501, 502], "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        self.course = response.json
        state = self.client.get("/api/state").json
        self.group = state["course_groups"][0]
        self.term = self.service.catalog()["terms"][0]
        self.sections = {item["number"]: item for item in self.service.catalog()["sections"]}
        self.path = self.rosters.path
        self.documents = self.app.extensions["documents"]
        self.exams = self.app.extensions["exams"]
        self.assignment = self.documents.create_assignment(self.term["definition_id"], "calendar-exam", "Calendar exam")

    def feed(self, start="2026-09-01", end="2026-09-30"):
        response = self.client.get("/api/calendar", query_string={"start": start, "end": end})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json

    def custom(self, **updates):
        data = {"request_id": str(uuid.uuid4()), "scope": "workspace", "category": "event",
                "title": "Office planning", "date": "2026-09-15", "all_day": True, **updates}
        response = self.client.post("/api/calendar/events", json=data, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def exam(self, date="2026-09-15", time="2 PM"):
        event = self.exams.create({"term_id": self.term["id"], "assignment_id": self.assignment["id"],
                                  "section_ids": [self.sections[501]["id"], self.sections[502]["id"]]})
        return self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Main", "kind": "main",
                                                     "date": date, "time": time, "location": "HECC 105"})

    def chart(self, date="2026-09-15", time="2 PM"):
        self.roster()
        room = self.app.extensions["rooms"].create("HECC 105", "Private room note", chart_bytes({"A1": "A1", "B1": "A2"}), "room.xlsx")
        chart = self.app.extensions["seating"].create({"assignment_id": self.assignment["id"],
                    "section_ids": [self.sections[501]["id"]], "room_id": room["id"], "notes": "Private chart note"})
        # Seeding the schedule does not confirm a chart or issue response cards.
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date=?,exam_time=? WHERE id=?", (date, time, chart["id"]))
        return chart

    def roster(self, section=501):
        rows = []
        for name, uin in (("PrivateStudent, Alice", "001234567"), ("PrivateStudent, Bob", "123456789")):
            row = [""] * len(COLUMNS)
            row[1], row[2] = name, uin
            rows.append(row)
        response = self.client.post(f"/api/course-groups/{self.group['id']}/sections/{section}/roster",
            data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)

    def import_dr(self, rows):
        content = workbook(rows)
        preview = self.client.post("/api/accommodations/exams/preview",
            data={"file": (io.BytesIO(content), "dr.xlsx"), "term": "null"}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        response = self.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "dr.xlsx"), "term": "null",
            "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(response.status_code, 202, response.json)
        self.app.extensions["inbox"].process_one()
        job = self.app.extensions["inbox"].get(response.json["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))

    def test_range_validation_includes_both_endpoints_and_caps_inclusive_year(self):
        first = self.custom(date="2026-09-01")
        last = self.custom(date="2026-09-30")
        self.custom(date="2026-08-31")
        self.custom(date="2026-10-01")
        self.assertEqual({event["id"] for event in self.feed()["events"]}, {first["id"], last["id"]})
        self.assertEqual([event["id"] for event in self.feed("2026-09-30", "2026-09-30")["events"]], [last["id"]])
        self.feed("2024-01-01", "2024-12-31")  # A complete leap year is 366 dates.
        for args in ({}, {"start": "2026-09-01"}, {"start": "2026-02-30", "end": "2026-03-01"},
                     {"start": "2026-9-01", "end": "2026-09-30"}, {"start": "2026-09-30", "end": "2026-09-01"},
                     {"start": "2024-01-01", "end": "2025-01-01"}, {"start": "2026-W01-1", "end": "2026-09-30"}):
            with self.subTest(args=args):
                self.assertEqual(self.client.get("/api/calendar", query_string=args).status_code, 400)

    def test_today_uses_campus_date_and_schedule_time_is_not_converted(self):
        exam = self.exam(time="2 PM")
        with patch("workspace_calendar.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 19, 0, 30, tzinfo=timezone.utc)
            feed = self.feed()
        self.assertEqual((feed["today"], feed["time_zone"]), ("2026-09-18", "America/Chicago"))
        event, = feed["events"]
        self.assertEqual((event["date"], event["time"], event["time_basis"]), ("2026-09-15", "2 PM", "campus"))
        self.assertEqual(event["id"], "exam:" + exam["sittings"][0]["id"])
        self.assertEqual(event["href"], "#exams/" + exam["id"])
        self.assertEqual((event["exam_href"], event["assignment_id"]), ("#exams/" + exam["id"], exam["assignment_id"]))
        self.assertEqual(event["sections"], [501, 502])
        self.assertFalse(event["editable"])
        self.assertFalse(event["all_day"])

    def test_seating_dedup_requires_exact_explicit_sitting_and_wall_clock_match(self):
        exam, chart = self.exam(), self.chart()
        self.assertEqual({event["source"] for event in self.feed()["events"]}, {"exam", "seating"})
        self.exams.attach_chart(exam["id"], {"version": exam["version"], "chart_id": chart["id"], "sitting_id": exam["sittings"][0]["id"]})
        self.assertEqual([event["source"] for event in self.feed()["events"]], ["exam"])
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_time='14:00' WHERE id=?", (chart["id"],))
        events = self.feed()["events"]
        self.assertEqual({event["source"] for event in events}, {"exam", "seating"})
        seating = next(event for event in events if event["source"] == "seating")
        self.assertEqual((seating["time"], seating["sections"], seating["notes"]), ("14:00", [501], ""))
        # The chart's assessment has an exam record in this term, so its Exams hub is that record.
        self.assertEqual(seating["exam_href"], "#exams/" + exam["id"])
        self.assertNotIn("Private", json.dumps(events))
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-09-16',exam_time='2 PM' WHERE id=?", (chart["id"],))
        self.assertEqual({event["date"] for event in self.feed()["events"]}, {"2026-09-15", "2026-09-16"})

    def test_chart_attached_to_event_without_sitting_keeps_its_date(self):
        exam, chart = self.exam(), self.chart()
        self.exams.attach_chart(exam["id"], {"version": exam["version"], "chart_id": chart["id"]})
        self.assertEqual({event["source"] for event in self.feed()["events"]}, {"exam", "seating"})

    def test_dr_latest_revisions_aggregate_by_schedule_and_exclude_private_fields(self):
        self.roster()
        self.roster(502)
        first = booking(Section="501", **{"Exam Type": "Calendar exam"})
        second = booking(Section="501", UIN="123456789", **{"Exam Type": "Calendar exam", "Last Name": "PrivateStudent", "First Name": "Bob"})
        other_section = booking(Section="502", **{"Exam Type": "Calendar exam"})
        self.import_dr([first, second, other_section])
        with connect(self.path) as db:
            private_ids = [r[0] for r in db.execute("SELECT id FROM dr_exam_bookings")] + [r[0] for r in db.execute("SELECT enrollment_id FROM dr_exam_bookings")]
        events = [event for event in self.feed()["events"] if event["source"] == "dr"]
        self.assertEqual(sorted((event["sections"], event["booking_count"]) for event in events), [([501], 2), ([502], 1)])
        self.assertEqual({(event["date"], event["time"], event["end_time"], event["time_basis"]) for event in events}, {("2026-09-17", "19:05", "20:58", "dr_center")})
        # A DR row opens the DR exam schedule filtered to its course and exam; the link names no student.
        for event in events:
            path, query = event["href"].split("?", 1)
            self.assertEqual(path, "#accommodations")
            self.assertEqual(parse_qs(query), {"tab": ["exams"], "course": [event["code"]], "exam": ["Calendar exam"], "when": ["all"]})
            self.assertNotIn(" ", event["href"])
        serialized = json.dumps(self.feed())
        for private in ["PrivateStudent", "001234567", "123456789", "requested_on", "source_fields", "alice@example.test", *private_ids]:
            self.assertNotIn(private, serialized)
        old_ids = {tuple(event["sections"]): event["id"] for event in events}
        self.import_dr([{**first, "Date": datetime(2026, 9, 18), "Campus": "Other center"}])
        events = [event for event in self.feed()["events"] if event["source"] == "dr"]
        self.assertEqual(len(events), 3)
        self.assertEqual(sum(event["booking_count"] for event in events), 3)
        unchanged = next(event for event in events if event["sections"] == [502])
        self.assertEqual(unchanged["id"], old_ids[(502,)])
        moved = next(event for event in events if event["date"] == "2026-09-18")
        self.assertEqual((moved["booking_count"], moved["location"]), (1, "Other center"))
        self.assertEqual(len([event for event in self.feed("2026-09-17", "2026-09-17")["events"] if event["source"] == "dr"]), 2)

    def test_custom_overlap_scopes_and_stale_mutation_responses(self):
        event = self.custom(date="2026-08-30", end_date="2026-09-03", scope="section", definition_id=self.term["definition_id"],
                            term_id=self.term["id"], section_id=self.sections[501]["id"], title="Plan notes")
        self.assertEqual(event["id"], "custom:" + event["custom_id"])
        self.assertEqual((event["sections"], event["code"], event["term_label"]), ([501], "MATH 140", "Fall 2026"))
        self.assertEqual([item["id"] for item in self.feed()["events"]], [event["id"]])
        url = "/api/calendar/events/" + event["custom_id"]
        changed = self.client.put(url, json={"version": event["version"], "title": "Revised notes"}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(changed.json["title"], "Revised notes")
        self.assertEqual(self.client.put(url, json={"version": event["version"], "title": "Stale"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.delete(url, json={"version": event["version"]}, headers=HEADERS).status_code, 409)
        deleted = self.client.delete(url, json={"version": changed.json["version"]}, headers=HEADERS)
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual((deleted.json["id"], deleted.json["deleted"]), (event["id"], True))
        self.assertEqual(self.feed()["events"], [])
        self.assertEqual(self.client.put(url, json={"version": deleted.json["version"], "title": "Restore"}, headers=HEADERS).status_code, 404)

    def test_custom_requests_are_idempotent_and_json_is_required(self):
        key = str(uuid.uuid4())
        first = self.custom(request_id=key)
        self.assertEqual(self.custom(request_id=key)["id"], first["id"])
        self.assertEqual(len(self.feed()["events"]), 1)
        response = self.client.post("/api/calendar/events", json={"request_id": key, "title": "Different", "date": "2026-09-15"}, headers=HEADERS)
        self.assertEqual(response.status_code, 409)
        for body in ([], None, "event"):
            self.assertEqual(self.client.post("/api/calendar/events", data=json.dumps(body), content_type="application/json", headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/calendar/events", data="title=wrong", headers=HEADERS).status_code, 415)

    def test_source_schedule_cannot_be_mutated_through_calendar(self):
        exam = self.exam()
        event, = self.feed()["events"]
        for identifier in (event["id"], exam["sittings"][0]["id"]):
            url = "/api/calendar/events/" + identifier
            self.assertEqual(self.client.put(url, json={"version": 1, "date": "2026-10-10"}, headers=HEADERS).status_code, 404)
            self.assertEqual(self.client.delete(url, json={"version": 1}, headers=HEADERS).status_code, 404)
        self.assertEqual(self.feed()["events"], [event])

    def test_direct_access_and_same_origin_write_protection(self):
        fresh = self.app.test_client()
        self.assertEqual(fresh.get("/api/calendar?start=2026-09-01&end=2026-09-30").status_code, 200)
        event = self.custom()
        for method, url, body in (("POST", "/api/calendar/events", {"title": "Blocked"}),
                                 ("PUT", "/api/calendar/events/" + event["custom_id"], {"version": event["version"], "title": "Blocked"}),
                                 ("DELETE", "/api/calendar/events/" + event["custom_id"], {"version": event["version"]})):
            for headers in ({}, {**HEADERS, "Origin": "https://elsewhere.test"}, {**HEADERS, "Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(fresh.open(url, method=method, json=body, headers=headers).status_code, 403)
        self.assertEqual(self.feed()["events"], [event])

    def test_catalog_only_reads_do_not_open_roster_or_document_payloads(self):
        self.exam()
        with ExitStack() as stack:
            for target, name in ((self.rosters, "get"), (self.app.extensions["inbox"], "read_artifact"),
                                 (self.app.extensions["inbox"], "read_original")):
                stack.enter_context(patch.object(target, name, side_effect=AssertionError("Calendar must not read payloads")))
            self.assertEqual(len(self.feed()["events"]), 1)

    def test_archived_term_dates_remain_visible_as_history(self):
        self.exam()
        response = self.client.patch("/api/courses/" + self.course["id"], json={"version": self.course["version"], "is_current": False}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(self.feed()["events"]), 1)


class CalendarStorageGuardTests(unittest.TestCase):
    def test_calendar_retains_existing_storage_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = temporary_storage(temporary)
            app = create_app(storage=storage)
            if "workspace_calendar" not in app.blueprints:
                register(app, CalendarService(app.extensions["rosters"]))
            client = app.test_client()
            with patch.object(storage, "check_hdd", side_effect=StorageError("Backup drive unavailable")):
                response = client.get("/api/calendar?start=2026-09-01&end=2026-09-30")
                self.assertEqual(response.status_code, 503, response.json)
                response = client.post("/api/calendar/events", json={"title": "Blocked"}, headers=HEADERS)
                self.assertEqual(response.status_code, 503, response.json)
