"""Run with: python -m unittest discover -s tests -v"""

import tempfile
import unittest
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app import create_app


HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()

    def state(self):
        return self.client.get("/api/state").json

    def test_a_slow_request_goes_in_the_journal_and_a_quick_one_does_not(self):
        # FRONTEND "A RESTART NOBODY SEES": waitress logs no requests.
        with self.assertNoLogs("app", "WARNING"):
            self.client.get("/api/state?include=core")
        with patch("app.SLOW_REQUEST", 0), self.assertLogs("app", "WARNING") as logged:
            self.client.get("/api/state?include=core")
        self.assertRegex(logged.output[0], r"GET /api/state took \d+\.\d s \(200\)")

    def create_tool(self, **overrides):
        return self.client.post("/api/tools", json={"name": "Test roster", **overrides}, headers=HEADERS)

    def create_course(self, **overrides):
        return self.client.post("/api/courses", json={
            "name": "Biology", "code": "BIO 101", "term": "Fall", "year": 2026, "sections": [1], **overrides,
        }, headers=HEADERS)

    def test_first_run_has_only_the_planned_roster_module(self):
        tools = self.state()["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "Course Rosters")
        self.assertEqual(tools[0]["status"], "planned")
        self.assertEqual(tools[0]["url"], "")

    def test_create_edit_pin_delete_and_restart_persistence(self):
        response = self.create_tool(description="Across all my courses", category="Administration")
        self.assertEqual(response.status_code, 201)
        tool = response.json
        response = self.client.patch(f"/api/tools/{tool['id']}", json={
            "url": "https://rosters.example.ts.net", "status": "ready", "pinned": True, "version": 1,
        }, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["version"], 2)
        second_client = create_app(self.directory.name).test_client()
        saved = next(item for item in second_client.get("/api/state").json["tools"] if item["id"] == tool["id"])
        self.assertTrue(saved["pinned"])
        self.assertEqual(saved["status"], "ready")
        response = self.client.delete(f"/api/tools/{tool['id']}", json={"version": 2}, headers=HEADERS)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(self.state()["tools"]), 1)

    def test_empty_workspace_stays_empty_after_restart(self):
        tool = self.state()["tools"][0]
        self.client.delete(f"/api/tools/{tool['id']}", json={"version": 1}, headers=HEADERS)
        restarted = create_app(self.directory.name).test_client()
        self.assertEqual(restarted.get("/api/state").json["tools"], [])

    def test_ready_requires_a_url_and_invalid_inputs_do_not_persist(self):
        before = self.state()
        for data in ({"name": ""}, {"name": 123}, {"status": "ready"}, {"category": "not a category"},
                     {"status": []}, {"pinned": "false"}, {"name": "x" * 61}, {"description": "x" * 281}):
            with self.subTest(data=data):
                self.assertEqual(self.create_tool(**data).status_code, 400)
        self.assertEqual(self.state(), before)

    def test_unsafe_urls_are_rejected(self):
        for url in ("javascript:alert(1)", "data:text/html,test", "//evil.example", "/\\evil.example", "ftp://host",
                    "https://user:password@host", "https://host:bad", "https://host:99999", "https://[bad", "https://host/a\nb", "https://host/a b"):
            with self.subTest(url=url):
                self.assertEqual(self.create_tool(url=url).status_code, 400)

    def test_supported_urls_and_browser_escaped_text_are_stored_as_data(self):
        for url in ("/rosters/", "https://home.example.ts.net/rosters?q=fall", "http://100.64.0.1:8000", "http://[::1]:3001"):
            with self.subTest(url=url):
                self.assertEqual(self.create_tool(url=url, status="ready").status_code, 201)
        title = '<img src=x onerror="alert(1)">'
        self.assertEqual(self.create_tool(name=title).json["name"], title)

    def test_cross_site_writes_and_form_posts_are_blocked(self):
        cases = ({}, {"X-Teaching-Request": "1", "Origin": "https://evil.example"},
                 {"X-Teaching-Request": "1", "Sec-Fetch-Site": "cross-site"},
                 {"X-Teaching-Request": "1", "Origin": "null"})
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post("/api/tools", json={"name": "Bad"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/tools", data={"name": "Bad"}, headers=HEADERS).status_code, 415)

    def test_same_origin_https_proxy_request_is_supported(self):
        response = self.client.post("/api/tools", json={"name": "Tailnet tool"}, headers={
            "X-Teaching-Request": "1", "Origin": "https://home.example.ts.net", "Host": "home.example.ts.net",
        })
        self.assertEqual(response.status_code, 201)

    def test_concurrent_edits_and_stale_delete_are_rejected(self):
        tool = self.state()["tools"][0]
        def update(name):
            with self.app.test_client() as client:
                return client.patch(f"/api/tools/{tool['id']}", json={"name": name, "version": 1}, headers=HEADERS).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(update, ("First edit", "Second edit"))), [200, 409])
        response = self.client.delete(f"/api/tools/{tool['id']}", json={"version": 1}, headers=HEADERS)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.state()["tools"]), 1)

    def test_settings_persist_and_reject_stale_updates(self):
        data = {"workspace_name": "Fall teaching", "teacher_name": "Alex", "version": 1}
        response = self.client.patch("/api/settings", json=data, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["version"], 2)
        self.assertEqual(self.client.patch("/api/settings", json=data, headers=HEADERS).status_code, 409)
        self.assertEqual(create_app(self.directory.name).test_client().get("/api/state").json["settings"]["teacher_name"], "Alex")

    def test_saving_settings_reads_only_the_saved_settings(self):
        # PERFORMANCE: a saved profile must not fail while projecting unrelated rooms or seating afterward.
        with patch.object(self.app.extensions["rooms"], "list", side_effect=AssertionError("unrelated rooms read")), \
             patch.object(self.app.extensions["seating"], "list", side_effect=AssertionError("unrelated seating read")):
            response = self.client.patch("/api/settings", json={"workspace_name": "Teaching", "teacher_name": "Alex", "version": 1}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"workspace_name": "Teaching", "teacher_name": "Alex", "version": 2})

    def test_export_contains_saved_data_and_static_files_do_not_expose_database(self):
        export = self.client.get("/api/export")
        self.assertEqual(export.status_code, 200)
        self.assertIn("attachment", export.headers["Content-Disposition"])
        self.assertEqual(export.json["schema_version"], 4)
        self.assertEqual(export.json["tools"], self.state()["tools"])
        self.assertEqual(self.client.get("/api/health").json, {"status": "ok"})
        for path in ("/data/faculty.sqlite3", "/static/../app.py", "/.env"):
            self.assertEqual(self.client.get(path).status_code, 404)
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        # An ordinary page and a view's own policy: the app-wide one is only the default (COMMUNICATIONS THE TIMELINE, 2026-11-06).
        with self.client.get("/api/health") as response:
            self.assertEqual(response.headers["Content-Security-Policy"].split(";")[0], "default-src 'self'")
        from communications.api import download as kept_download
        with self.app.test_request_context("/api/communications/messages/1/original"):
            response = self.app.process_response(kept_download("a.pdf", b"x", "application/pdf"))
            self.assertEqual(response.headers["Content-Security-Policy"], "default-src 'none'; sandbox; frame-ancestors 'none'")
            self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="a.pdf"')

    def test_course_group_summary_is_a_read_only_route(self):
        rules = {rule.rule: rule.methods for rule in self.app.url_map.iter_rules()}
        self.assertIn("/api/course-groups/summary", rules)
        self.assertEqual(rules["/api/course-groups/summary"] - {"HEAD", "OPTIONS"}, {"GET"})
        self.assertEqual(self.client.get("/api/course-groups/summary").json, {"groups": {}})
        course = self.create_course().json
        groups = self.client.get("/api/course-groups/summary").json["groups"]
        group = next(item for item in self.state()["course_groups"] if item["offerings"][0]["id"] == course["id"])
        self.assertEqual(groups[group["id"]]["sections"]["1"]["students"], 0)
        self.assertEqual(self.client.post("/api/course-groups/summary", json={}, headers=HEADERS).status_code, 405)

    def test_malformed_json_missing_tools_and_oversized_bodies(self):
        self.assertEqual(self.client.post("/api/tools", json=[], headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/tools", data="{", content_type="application/json", headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.patch("/api/tools/missing", json={"version": 1}, headers=HEADERS).status_code, 404)
        self.assertEqual(self.create_tool(description="x" * 40000).status_code, 413)

    def test_courses_persist_and_can_be_marked_no_longer_current(self):
        self.assertEqual(self.state()["courses"], [])
        response = self.create_course(is_current=True)
        self.assertEqual(response.status_code, 201)
        course = response.json
        self.assertTrue(course["is_current"])
        restarted = create_app(self.directory.name).test_client()
        self.assertEqual(restarted.get("/api/state").json["courses"], [course])
        response = self.client.patch(f"/api/courses/{course['id']}", json={
            "is_current": False, "version": course["version"],
        }, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["is_current"])
        self.assertEqual(response.json["term"], "Fall")
        self.assertEqual(response.json["year"], 2026)
        self.assertEqual(self.client.get("/api/export").json["courses"], [response.json])
        self.assertEqual(len(self.state()["courses"]), 1)

    def test_course_validation_conflicts_and_deletion(self):
        for data in ({"name": ""}, {"name": "x" * 121}, {"name": "Biology", "is_current": "false"},
                     {"name": "Biology", "term": 2026}):
            with self.subTest(data=data):
                self.assertEqual(self.create_course(**data).status_code, 400)
        self.assertEqual(self.state()["courses"], [])
        course = self.create_course().json
        route = f"/api/courses/{course['id']}"
        self.assertEqual(self.client.patch(route, json={"name": "Biology II", "version": 1}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.patch(route, json={"is_current": False, "version": 1}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.delete(route, json={"version": 1}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.delete(route, json={"version": 2}, headers=HEADERS).status_code, 204)
        self.assertEqual(self.state()["courses"], [])

    def test_course_required_fields_and_term_choices(self):
        for field in ("name", "code", "term", "year", "sections"):
            with self.subTest(field=field):
                data = {"name": "Biology", "code": "BIO 101", "term": "Fall", "year": 2026, "sections": [1]}
                data.pop(field)
                self.assertEqual(self.client.post("/api/courses", json=data, headers=HEADERS).status_code, 400)
        for data in ({"code": " "}, {"term": "Autumn"}, {"year": "2026"}, {"year": True},
                     {"year": 2026.5}, {"year": 999}, {"year": 10000}, {"term": "Other"}, {"term_other": 123}):
            with self.subTest(data=data):
                self.assertEqual(self.create_course(**data).status_code, 400)
        for term in ("Fall", "Spring", "Summer I", "Summer II", "Summer (Full)"):
            with self.subTest(term=term):
                response = self.create_course(term=term, term_other="Unused")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json["term_other"], "")
                self.assertEqual(response.json["crn"], "")

    def test_crn_custom_term_and_multiple_sections_survive_restart_and_edit(self):
        response = self.create_course(term="Other", term_other="Winter intensive", year=2027,
                                      crn="00123", multiple_sections=True, sections=[1, 2, 7])
        self.assertEqual(response.status_code, 201)
        course = response.json
        restarted = create_app(self.directory.name).test_client()
        self.assertEqual(restarted.get("/api/state").json["courses"], [course])
        self.assertEqual(course["crn"], "00123")
        self.assertEqual(course["sections"], [1, 2, 7])
        route = f"/api/courses/{course['id']}"
        response = self.client.patch(route, json={"name": "Biology II", "version": 1}, headers=HEADERS)
        self.assertEqual(response.json["term_other"], "Winter intensive")
        self.assertEqual(response.json["sections"], [1, 2, 7])
        response = self.client.patch(route, json={"term": "Spring", "multiple_sections": False, "sections": [1], "version": 2}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["term_other"], "")
        self.assertEqual(response.json["sections"], [1])
        self.assertEqual(self.client.get("/api/export").json["courses"], [response.json])

    def test_multiple_sections_require_distinct_positive_whole_numbers(self):
        for sections in (None, "1,2", [], [1], [1, 1], [1, "2"], [1, 2.5], [0, 2], [-1, 2], [True, 2], [1, 9007199254740992]):
            with self.subTest(sections=sections):
                self.assertEqual(self.create_course(multiple_sections=True, sections=sections).status_code, 400)
        self.assertEqual(self.create_course(multiple_sections="yes", sections=[1, 2]).status_code, 400)
        self.assertEqual(self.state()["courses"], [])
        self.assertEqual(self.create_course(multiple_sections=False, sections=[1, 2]).status_code, 400)

    def test_single_section_is_required_and_saved_for_current_and_other_courses(self):
        for sections in (None, [], "1", [0], [-1], [True], [1.5], ["1"], [1, 2]):
            with self.subTest(sections=sections):
                self.assertEqual(self.create_course(sections=sections).status_code, 400)
        current = self.create_course(name="Current biology", sections=[4], is_current=True).json
        other = self.create_course(name="Past biology", sections=[7], is_current=False).json
        self.assertEqual(current["sections"], [4])
        self.assertEqual(other["sections"], [7])
        self.assertFalse(current["multiple_sections"])
        self.assertFalse(other["multiple_sections"])
        saved = create_app(self.directory.name).test_client().get("/api/state").json["courses"]
        self.assertEqual({course["id"] for course in saved}, {current["id"], other["id"]})
        self.assertEqual([course["id"] for course in saved if course["is_current"]], [current["id"]])

    def test_old_course_schema_migrates_without_guessing_missing_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "faculty.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE courses (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL, term TEXT NOT NULL,
                    is_current INTEGER NOT NULL, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                for identifier, term in (("known", "Fall 2026"), ("custom", "Winter intensive 2025"),
                                         ("undated", "Special session"), ("empty", "")):
                    db.execute("INSERT INTO courses VALUES (?, ?, '', ?, 1, 1, 'created', 'updated')",
                               (identifier, "Saved course", term))
            migrated = create_app(directory).test_client().get("/api/state").json["courses"]
            records = {course["id"]: course for course in migrated}
            self.assertEqual((records["known"]["term"], records["known"]["year"]), ("Fall", 2026))
            self.assertEqual((records["custom"]["term"], records["custom"]["term_other"], records["custom"]["year"]),
                             ("Other", "Winter intensive", 2025))
            self.assertEqual(records["undated"]["term_other"], "Special session")
            self.assertIsNone(records["undated"]["year"])
            self.assertEqual(records["empty"]["term"], "")
            for course in migrated:
                self.assertEqual(course["code"], "")
                self.assertEqual(course["crn"], "")
                self.assertEqual(course["sections"], [])
                self.assertEqual(course["version"], 2)
                self.assertEqual(course["created_at"], "created")
            self.assertEqual(create_app(directory).test_client().get("/api/state").json["courses"], migrated)


if __name__ == "__main__":
    unittest.main()
