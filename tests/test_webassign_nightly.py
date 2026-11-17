"""The WebAssign import of the morning sync (gradebook/webassign_nightly.py, run by gradebook/canvas_nightly.py; docs/decisions/
canvas.md WEBASSIGN NIGHTLY and NIGHTLY SYNC), offline against the loopback FakeCanvas, for a term no sync has written to yet: which
assignments are WebAssign's, the campus clock (from 5 am until 8 am since 2026-10-08, the 10 pm import folded in), what a morning
imports (Canvas always wins: the column mirrors Canvas, requirement, 2026-10-06), what waits for the instructor, a signed-out morning
tried again, due dates that follow Canvas (WebAssign's and, since 2026-10-08, Canvas quizzes'), and Sync with Canvas never sending to
WebAssign's assignments."""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_canvas import GradebookCanvasBase
from test_canvas_sync import SyncBase
from test_storage import HEADERS

from canvas import web
from gradebook import canvas_nightly
from rosters import connect

WEBASSIGN = "https://gateway.cengage.com/rest/lti/launch?ref=1"  # as MATH 140's 26 WebAssign assignments named it, 2026-10-04
GRADESCOPE = "https://www.gradescope.com/auth/lti/callback"
# Oct 5, 2026 in Central daylight time (UTC-5): 4:55 am, 5:05 am and 8:05 am.
EVENING = datetime(2026, 10, 5, 9, 55, tzinfo=timezone.utc)
NIGHT = datetime(2026, 10, 5, 10, 5, tzinfo=timezone.utc)
LATE = datetime(2026, 10, 5, 13, 5, tzinfo=timezone.utc)


class ToolTests(unittest.TestCase):
    def test_the_tool_host_decides_not_the_submission_type(self):
        def host(url, kinds=("external_tool",)):
            return web.tool_host({"submission_types": list(kinds), "external_tool_tag_attributes": {"url": url}})
        self.assertEqual(host(WEBASSIGN), "gateway.cengage.com")
        self.assertTrue(web.graded_by_webassign(host(WEBASSIGN)))
        self.assertTrue(web.graded_by_webassign(host("https://www.webassign.net/v4cgi/lti.pl")))  # the older integration
        self.assertFalse(web.graded_by_webassign(host(GRADESCOPE)))  # also an outside tool, not WebAssign
        self.assertFalse(web.graded_by_webassign(host("https://cengage.com.example.test/x")))
        self.assertEqual(host(WEBASSIGN, ("online_upload",)), "")  # Canvas takes the work itself
        self.assertEqual(web.tool_host({"submission_types": ["external_tool"]}), "")
        self.assertFalse(web.graded_by_webassign(""))


class NightlyBase(GradebookCanvasBase):
    def setUp(self):
        super().setUp()
        f = self.fake
        self.wa_a = f.add_assignment(self.course_a, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN)
        self.wa_b = f.add_assignment(self.course_b, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN)
        self.gs_a = f.add_assignment(self.course_a, "Take-home quiz", 10, group="Quizzes", tool=GRADESCOPE)
        f.grade(self.course_a, self.wa_a, self.alice, 9.5)
        f.grade(self.course_a, self.wa_a, self.bob, 4)
        f.grade(self.course_b, self.wa_b, self.carol, missing=True)
        f.grade(self.course_a, self.gs_a, self.alice, 7)
        self.nightly = self.app.extensions["canvas_nightly"]

    def runs(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in
                    db.execute("SELECT night,kind,status,detail_json,request_ref FROM gradebook_canvas_nightly_runs ORDER BY created_at,rowid")]

    def view(self):
        return self.client.get(self.base).json

    def cell(self, view, title, name):
        item = next(i["id"] for i in view["setup"]["items"] if i["title"] == title)
        return (view["cells"].get(item) or {}).get(self.rows[name])

    def type(self, title, name, **value):
        view = self.view()
        item = next(i["id"] for i in view["setup"]["items"] if i["title"] == title)
        current = self.cell(view, title, name)
        answer = self.client.post(self.base + "/scores", json={"request_id": f"t-{time.monotonic_ns()}", "changes": [
            {"item_id": item, "row": self.rows[name], "base": current["id"] if current else None, **value}]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)

    def tonight(self):
        """A round at the real time, the morning's gate opened (the retry wait compares the real clock with recorded times)."""
        with patch.object(canvas_nightly, "HOUR", 0), patch.object(canvas_nightly, "END", 24):
            return self.nightly.round()


class NightlyTests(NightlyBase):
    def test_from_five_am_campus_time_once_a_morning_only_webassigns_assignments(self):
        self.assertEqual(self.nightly.round(EVENING), {})
        self.assertEqual(self.nightly.round(LATE), {})  # after 8 am: the next morning
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.nightly.round(NIGHT), {self.term: "imported"})
        view = self.view()
        titles = [item["title"] for item in view["setup"]["items"]]
        # One new column for both courses' WebAssign assignment, in a category named after its assignment group; nothing else.
        self.assertEqual(titles, ["Quiz 1", "Exam 1", "Sect. 1.1 Matrices"])
        self.assertEqual(view["setup"]["items"][2]["canvas"], "webassign")  # read-only in Faculty from now on
        category = {c["id"]: c["name"] for c in view["setup"]["categories"]}
        self.assertEqual(category[view["setup"]["items"][2]["category_id"]], "WebAssign Homework")
        self.assertEqual(self.cell(view, "Sect. 1.1 Matrices", "Example, Alice")["points"], "9.5")
        self.assertEqual(self.cell(view, "Sect. 1.1 Matrices", "Example, Bob")["points"], "4")
        self.assertIsNone(self.cell(view, "Sect. 1.1 Matrices", "Example, Carol"))  # Canvas's missing flag is not recorded
        self.assertIsNone(self.cell(view, "Quiz 1", "Example, Alice"))  # Canvas's Quiz 1 is not WebAssign's: not read in
        [run] = self.runs()
        self.assertEqual((run["night"], run["kind"], run["status"], run["detail"]), ("2026-10-05", "import", "imported",
                                                                                      {"assignments": 2, "waiting": [], "written": 2, "new_columns": 1}))
        with connect(self.app.config["DATABASE"]) as db:
            summary = json.loads(db.execute("SELECT summary_json FROM gradebook_requests WHERE id=?", (run["request_ref"],)).fetchone()[0])
        self.assertEqual((summary["source"], summary["automatic"]), ("canvas", "webassign"))
        changes = self.client.get(self.base + "/changes").json
        self.assertTrue(any(entry.get("summary", {}).get("automatic") == "webassign" for entry in changes["changes"]))
        self.assertEqual(self.nightly.round(NIGHT + timedelta(minutes=30)), {})  # this morning is done
        self.assertEqual(self.view()["canvas"]["nightly"], {"night": "2026-10-05", "kind": "import", "status": "imported", "at": run_at(self),
                                                            "waiting": [], "waiting_keys": [], "written": 2, "new_columns": 1})
        # The next night with nothing new records the night and no request.
        requests = self.request_count()
        self.assertEqual(self.nightly.round(NIGHT + timedelta(days=1)), {self.term: "unchanged"})
        self.assertEqual(self.request_count(), requests)

    def request_count(self):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute("SELECT COUNT(*) FROM gradebook_requests").fetchone()[0]

    def test_canvas_always_wins_and_its_changes_come_in(self):
        self.nightly.round(NIGHT)  # nights counted from NIGHT follow (a real-clock night could be one of them)
        view = self.view()
        item = next(i["id"] for i in view["setup"]["items"] if i["title"] == "Sect. 1.1 Matrices")
        current = self.cell(view, "Sect. 1.1 Matrices", "Example, Bob")
        refused = self.client.post(self.base + "/scores", json={"request_id": "typed-wa", "changes": [
            {"item_id": item, "row": self.rows["Example, Bob"], "base": current["id"], "status": "scored", "points": "6"}]}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400, refused.json)  # read-only in Faculty: WebAssign's scores come from Canvas
        self.fake.grade(self.course_a, self.wa_a, self.alice, 10)
        self.fake.grade(self.course_a, self.wa_a, self.bob, None)  # taken out in Canvas: taken out here too
        self.assertEqual(self.nightly.round(NIGHT + timedelta(days=1)), {self.term: "imported"})
        view = self.view()
        self.assertEqual(self.cell(view, "Sect. 1.1 Matrices", "Example, Alice")["points"], "10")
        self.assertIsNone(self.cell(view, "Sect. 1.1 Matrices", "Example, Bob"))

    def test_an_assignment_the_titles_cannot_place_waits_and_the_rest_comes_in(self):
        no_points = self.fake.add_assignment(self.course_a, "Sect. 2.1 Lines", None, group="WebAssign Homework", tool=WEBASSIGN)
        self.fake.add_assignment(self.course_b, "Sect. 2.1 Lines", 0, group="WebAssign Homework", tool=WEBASSIGN)
        self.fake.grade(self.course_a, no_points, self.alice, 3)
        self.assertEqual(self.tonight(), {self.term: "imported"})
        self.assertEqual(len(self.runs()[-1]["detail"]["waiting"]), 2)  # one per course
        self.assertEqual(self.view()["canvas"]["nightly"]["waiting"], ["Sect. 2.1 Lines"])  # named once
        self.assertEqual(self.cell(self.view(), "Sect. 1.1 Matrices", "Example, Alice")["points"], "9.5")
        # The instructor's own Sync with Canvas places it: the page stops naming it.
        key = f"{self.course_a}:{no_points}"
        plan = self.preview({"mapping": {key: {"action": "new", "title": "Sect. 2.1 Lines", "points": "10", "category_name": "WebAssign Homework"}}})
        self.ended(self.apply(plan, "place-lines"))
        self.assertEqual(self.view()["canvas"]["nightly"]["waiting"], ["Sect. 2.1 Lines"])  # still waiting in the other course
        key_b = next(c["key"] for c in self.preview()["from_canvas"]["columns"] if c["title"] == "Sect. 2.1 Lines" and c["course_id"] == self.course_b)
        line = next(i["id"] for i in self.view()["setup"]["items"] if i["title"] == "Sect. 2.1 Lines")
        plan = self.preview({"mapping": {key_b: {"action": "item", "item_id": line}}})
        self.ended(self.apply(plan, "place-lines-b"))
        self.assertEqual(self.view()["canvas"]["nightly"]["waiting"], [])

    def test_leave_out_skips_what_waits_from_now_on_and_a_chosen_column_brings_it_back(self):
        # Graphing Practice (requirement, 2026-10-04): no points in Canvas, in both courses.
        practice_a = self.fake.add_assignment(self.course_a, "Graphing Practice", 0, group="WebAssign Homework", tool=WEBASSIGN)
        practice_b = self.fake.add_assignment(self.course_b, "Graphing Practice", 0, group="WebAssign Homework", tool=WEBASSIGN)
        self.fake.grade(self.course_a, practice_a, self.alice, 1)
        self.nightly.round(NIGHT)
        shown = self.view()["canvas"]["nightly"]
        self.assertEqual(shown["waiting"], ["Graphing Practice"])
        keys = shown["waiting_keys"]
        self.assertEqual(sorted(entry["key"] for entry in keys), sorted([f"{self.course_a}:{practice_a}", f"{self.course_b}:{practice_b}"]))
        requests = self.request_count()
        reads = len(self.fake.requests)
        last_import = self.view()["canvas"]["last_import"]
        body = {"assignments": keys, "request_id": "leave-out-1"}
        answer = self.client.post(self.base + "/canvas/skip", json=body, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(answer.json["canvas"]["nightly"]["waiting"], [])
        self.assertEqual(answer.json["canvas"]["last_import"], last_import)  # imported nothing
        self.assertEqual(len(self.fake.requests), reads)  # Canvas was not read
        self.assertEqual(self.request_count(), requests + 1)
        self.assertEqual(self.client.post(self.base + "/canvas/skip", json=body, headers=HEADERS).status_code, 200)  # a replay
        self.assertEqual(self.client.post(self.base + "/canvas/skip", json={**body, "request_id": "leave-out-2"}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.request_count(), requests + 1)  # already left out: nothing recorded again
        with connect(self.app.config["DATABASE"]) as db:
            summary = json.loads(db.execute("SELECT summary_json FROM gradebook_requests WHERE request_id='leave-out-1'").fetchone()[0])
        self.assertEqual((summary["skipped"], summary["assignments"], summary["written"]), (["Graphing Practice"], 2, 0))
        other = self.client.post(self.base + "/canvas/skip", json={"assignments": [{"key": "999:1", "title": "X"}], "request_id": "leave-out-3"}, headers=HEADERS)
        self.assertEqual(other.status_code, 400, other.json)  # not a course this term links
        # The next night neither waits for it nor imports it; the instructor's Sync with Canvas suggests it as skipped before.
        self.assertEqual(self.nightly.round(NIGHT + timedelta(days=1))[self.term], "unchanged")
        self.assertEqual(self.view()["canvas"]["nightly"]["waiting"], [])
        self.assertNotIn("Graphing Practice", [item["title"] for item in self.view()["setup"]["items"]])
        column = next(c for c in self.preview()["from_canvas"]["columns"] if c["key"] == f"{self.course_a}:{practice_a}")
        self.assertEqual((column["mapping"], column["suggested"]), ({"action": "skip"}, "remembered"))
        # Choosing a column for it brings it back: a new column of its own, and the next night keeps it filled.
        plan = self.preview({"mapping": {column["key"]: {"action": "new", "title": "Graphing Practice", "points": "1",
                                                          "category_name": "WebAssign Homework"}}})
        self.ended(self.apply(plan, "practice-back"))
        self.assertEqual(self.cell(self.view(), "Graphing Practice", "Example, Alice")["points"], "1")
        self.fake.grade(self.course_a, practice_a, self.bob, 1)
        self.assertEqual(self.nightly.round(NIGHT + timedelta(days=2))[self.term], "imported")
        self.assertEqual(self.cell(self.view(), "Graphing Practice", "Example, Bob")["points"], "1")

    def test_a_signed_out_night_is_named_tried_again_hourly_and_cleared_by_signing_in(self):
        self.fake.sessions.clear()
        self.assertEqual(self.tonight(), {self.term: "signed_out"})
        shown = self.view()["canvas"]["nightly"]
        self.assertEqual((shown["status"], shown["signed_in_since"]), ("signed_out", False))
        self.assertEqual(self.tonight(), {})  # within the hour: not again
        web.save(self.service.settings, self.fake.session(), user={"name": "Pat Instructor"},
                 saved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2)))
        self.assertTrue(self.view()["canvas"]["nightly"]["signed_in_since"])
        with patch.object(canvas_nightly, "RETRY", 0):
            self.assertEqual(self.tonight(), {self.term: "imported"})
        self.assertEqual([run["status"] for run in self.runs()], ["signed_out", "imported"])

    def test_courses_without_webassign_record_the_night_and_write_nothing(self):
        for course in (self.course_a, self.course_b):
            for group in self.fake.groups[course]:
                group["assignments"] = [a for a in group["assignments"] if "external_tool_tag_attributes" not in a or "cengage" not in a["external_tool_tag_attributes"]["url"]]
        self.assertEqual(self.tonight(), {self.term: "none"})
        self.assertEqual([item["title"] for item in self.view()["setup"]["items"]], ["Quiz 1", "Exam 1"])

    def test_the_ten_pm_import_s_last_night_is_shown_until_a_morning_runs(self):
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("""INSERT INTO gradebook_nightly_runs(id,term_id,source,night,status,detail_json,request_ref,created_at)
                VALUES('old-run-1',?,'webassign','2026-10-04','signed_out','{}',NULL,'2026-10-05T03:05:00+00:00')""", (self.term,))
        self.assertEqual(self.view()["canvas"]["nightly"]["status"], "signed_out")
        self.nightly.round(NIGHT)
        self.assertEqual((self.view()["canvas"]["nightly"]["kind"], self.view()["canvas"]["nightly"]["status"]), ("import", "imported"))

    def test_a_term_that_is_not_current_is_left_alone(self):
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("UPDATE course_terms SET is_current=0")
        self.assertEqual(self.tonight(), {})
        self.assertNotIn("nightly", self.view()["canvas"])


SEP_4, SEP_8, SEP_11, SEP_25 = "2026-09-05T04:55:00Z", "2026-09-09T04:55:00Z", "2026-09-12T04:55:00Z", "2026-09-26T04:55:00Z"  # 11:55 pm Central


class DueDateTests(NightlyBase):
    """DUE DATES FOLLOW CANVAS (requirement, 2026-10-05): WebAssign sends a changed due date to Canvas; the night moves the gradebook column and Faculty's Canvas copy."""

    def move(self, course, assignment, when):
        self.fake.assignment(course, assignment)["due_at"] = when

    def due(self, title):
        return next(item["due_date"] for item in self.view()["setup"]["items"] if item["title"] == title)

    def night(self, days):
        return self.nightly.round(NIGHT + timedelta(days=days))[self.term]

    def save_due(self, title, date):
        view = self.view()
        setup = {key: view["setup"][key] for key in ("categories", "items", "mode", "ungraded", "scale", "letter_rounding") if key in view["setup"]}
        setup["items"] = [{**item, "due_date": date} if item["title"] == title else item for item in setup["items"]]
        saved = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": f"due-{date}",
                                                              "setup": setup}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)

    def test_a_column_takes_canvass_latest_date_and_a_date_typed_in_faculty_is_kept(self):
        self.move(self.course_a, self.wa_a, SEP_4)
        self.move(self.course_b, self.wa_b, SEP_4)
        self.assertEqual(self.nightly.round(NIGHT), {self.term: "imported"})
        self.assertEqual(self.due("Sect. 1.1 Matrices"), "2026-09-04")  # the new column, as Sync with Canvas makes it
        self.assertNotIn("due_dates", self.runs()[-1]["detail"])
        # WebAssign extends it for section 507's course: the column moves that night and the Changes page says why.
        self.move(self.course_a, self.wa_a, SEP_8)
        self.assertEqual(self.night(1), "unchanged")  # no score changed
        self.assertEqual(self.due("Sect. 1.1 Matrices"), "2026-09-08")
        self.assertEqual(self.runs()[-1]["detail"]["due_dates"], 1)
        entry = self.client.get(self.base + "/changes").json["changes"][0]
        self.assertEqual((entry["type"], entry["automatic"]), ("setup", "webassign"))
        self.assertTrue(any("due" in line and "Sep 8" in line for line in entry["changes"]), entry["changes"])
        # Section 508's course later still: one column, the latest date of its courses.
        self.move(self.course_b, self.wa_b, SEP_11)
        self.night(2)
        self.assertEqual(self.due("Sect. 1.1 Matrices"), "2026-09-11")
        self.assertEqual(self.night(3), "unchanged")
        self.assertNotIn("due_dates", self.runs()[-1]["detail"])  # nothing left to move
        # A date the instructor types is theirs: Canvas moving again changes nothing, and only the instructor's entry says "setup".
        self.save_due("Sect. 1.1 Matrices", "2026-09-20")
        self.move(self.course_a, self.wa_a, SEP_25)
        self.move(self.course_b, self.wa_b, SEP_25)
        self.night(4)
        self.assertEqual(self.due("Sect. 1.1 Matrices"), "2026-09-20")
        self.assertNotIn("automatic", self.client.get(self.base + "/changes").json["changes"][0])

    def test_a_column_made_without_a_date_takes_one_and_other_columns_are_left_alone(self):
        self.nightly.round(NIGHT)
        self.assertEqual(self.due("Sect. 1.1 Matrices"), "")  # Canvas had no due date then
        self.move(self.course_a, self.wa_a, SEP_4)
        self.move(self.course_a, self.gs_a, SEP_8)  # Gradescope's tool assignment: never WebAssign's
        self.night(1)
        self.assertEqual(self.due("Sect. 1.1 Matrices"), "2026-09-04")
        self.assertEqual(self.due("Exam 1"), "")

    def test_a_canvas_quiz_s_column_follows_canvas_too(self):
        """requirement, 2026-10-08, of the video quizzes ("Lecture Video and Quiz"): "Yes, follow Canvas"."""
        quiz_a = self.fake.add_assignment(self.course_a, "Lecture Video and Quiz 1", 2, group="Lecture Quizzes", quiz=True, due_at=SEP_4)
        self.fake.grade(self.course_a, quiz_a, self.alice, 2)
        self.nightly.round(NIGHT)
        self.assertEqual(self.due("Lecture Video and Quiz 1"), "2026-09-04")
        self.move(self.course_a, quiz_a, SEP_8)
        self.night(1)
        self.assertEqual(self.due("Lecture Video and Quiz 1"), "2026-09-08")
        self.assertEqual(self.runs()[-1]["detail"]["due_dates"], 1)

    # Faculty's Canvas copy --------------------------------------------------------------------------------------------------
    def start_copy(self):
        answer = self.client.post(f"/api/canvas/content/{self.term}/start", json={"course_id": self.course_a, "request_id": "copy-start-1",
                                                                                   "base_revision": None}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)

    def copy(self, name="Sect. 1.1 Matrices"):
        view = self.client.get(f"/api/canvas/content/{self.term}").json
        return view, next(a for a in view["content"]["assignments"] if a["name"] == name)

    def asked(self, name="Sect. 1.1 Matrices"):
        """Each course's push verdict on the assignment when the push would ask about it."""
        answer = self.client.post(f"/api/canvas/content/{self.term}/preview", json={"choices": {}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return {c["course_id"]: e["verdict"] for c in answer.json["courses"] for e in c["attention"] if e["label"] == name}

    def test_the_copy_follows_each_courses_date_and_the_push_finds_nothing_to_ask(self):
        self.move(self.course_a, self.wa_a, SEP_4)
        self.move(self.course_b, self.wa_b, SEP_4)
        self.start_copy()
        self.nightly.round(NIGHT)
        self.assertNotIn("copy_due_dates", self.runs()[-1]["detail"])
        # Section 507's course alone: its section gets its own date, the default stays for 508.
        self.move(self.course_a, self.wa_a, SEP_8)
        self.night(1)
        self.assertEqual(self.runs()[-1]["detail"]["copy_due_dates"], 1)
        view, assignment = self.copy()
        self.assertEqual((assignment["due_at"], assignment["section_due"]), (SEP_4, {self.sections[507]: SEP_8}))
        self.assertEqual(self.asked(), {})
        # Then 508's too: one date for every section again.
        self.move(self.course_b, self.wa_b, SEP_8)
        self.night(2)
        _, assignment = self.copy()
        self.assertEqual((assignment["due_at"], assignment["section_due"]), (SEP_8, {}))
        self.assertEqual(self.asked(), {})
        self.night(3)
        view, _ = self.copy()
        # The start, its columns' assignments (COLUMN ASSIGNMENTS: Quiz 1 linked by name, nothing new) and the two nights that
        # moved it; nothing more to move.
        self.assertEqual(view["revision"], 4)
        self.assertNotIn("copy_due_dates", self.runs()[-1]["detail"])
        # Recorded as alike again: the instructor's own later edit is an update for both courses, never "edited in Canvas too".
        content = view["content"]
        next(a for a in content["assignments"] if a["name"] == "Sect. 1.1 Matrices")["description"] = "<p>Due Sep 8.</p>"
        saved = self.client.post(f"/api/canvas/content/{self.term}", json={"base_revision": view["revision"], "request_id": "copy-edit-2",
                                                                            "content": content}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(self.asked(), {})
        answer = self.client.post(f"/api/canvas/content/{self.term}/preview", json={"choices": {}}, headers=HEADERS).json
        self.assertEqual(sorted(c["course_id"] for c in answer["courses"] for op in c["ops"] if op["op"] == "update_assignment"),
                         sorted([self.course_a, self.course_b]))

    def test_a_date_changed_in_the_copy_and_not_pushed_is_kept_and_other_tools_are_left_alone(self):
        self.move(self.course_a, self.wa_a, SEP_4)
        self.move(self.course_b, self.wa_b, SEP_4)
        self.start_copy()
        view, _ = self.copy()
        content = view["content"]
        next(a for a in content["assignments"] if a["name"] == "Sect. 1.1 Matrices")["due_at"] = SEP_11
        saved = self.client.post(f"/api/canvas/content/{self.term}", json={"base_revision": view["revision"], "request_id": "copy-edit-1",
                                                                            "content": content}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.move(self.course_a, self.wa_a, SEP_8)
        self.move(self.course_a, self.gs_a, SEP_8)
        self.nightly.round(NIGHT)
        self.assertNotIn("copy_due_dates", self.runs()[-1]["detail"])
        _, assignment = self.copy()
        self.assertEqual((assignment["due_at"], assignment["section_due"]), (SEP_11, {}))
        self.assertEqual(self.copy("Take-home quiz")[1]["due_at"], None)
        self.assertEqual(self.asked(), {self.course_a: "conflict"})  # the push asks, as before


    def test_webassign_s_available_dates_follow_too_and_the_push_finds_nothing_to_ask(self):
        """AVAILABILITY (requirement, 2026-10-17): WebAssign moved the available-from date in Canvas with the due date; the copy follows both."""
        sep_1, sep_6 = "2026-09-01T05:00:00Z", "2026-09-06T05:00:00Z"
        for course, assignment in ((self.course_a, self.wa_a), (self.course_b, self.wa_b)):
            self.move(course, assignment, SEP_4)
            self.fake.assignment(course, assignment)["unlock_at"] = sep_1
        self.fake.assignment(self.course_a, self.gs_a)["unlock_at"] = sep_1
        self.start_copy()
        self.nightly.round(NIGHT)
        for course, assignment in ((self.course_a, self.wa_a), (self.course_b, self.wa_b)):
            self.move(course, assignment, SEP_11)
            self.fake.assignment(course, assignment)["unlock_at"] = sep_6
        self.fake.assignment(self.course_a, self.gs_a)["unlock_at"] = sep_6  # not WebAssign's: left to the push
        self.night(1)
        self.assertEqual(self.runs()[-1]["detail"]["copy_due_dates"], 1)
        _, assignment = self.copy()
        self.assertEqual((assignment["due_at"], assignment["unlock_at"], assignment["lock_at"]), (SEP_11, sep_6, None))
        self.assertEqual(self.copy("Take-home quiz")[1]["unlock_at"], sep_1)
        self.assertEqual(self.asked(), {})
        # Only one course's moved: the copy has one date for every section, so it stays and the push asks, as before.
        self.fake.assignment(self.course_a, self.wa_a)["unlock_at"] = SEP_8
        self.night(2)
        self.assertEqual(self.copy()[1]["unlock_at"], sep_6)
        self.assertEqual(self.asked(), {self.course_a: "edited_in_canvas"})

    def test_an_available_date_changed_in_the_copy_and_not_pushed_is_kept(self):
        sep_1, sep_6 = "2026-09-01T05:00:00Z", "2026-09-06T05:00:00Z"
        for course, assignment in ((self.course_a, self.wa_a), (self.course_b, self.wa_b)):
            self.fake.assignment(course, assignment)["unlock_at"] = sep_1
        self.start_copy()
        view, _ = self.copy()
        content = view["content"]
        next(a for a in content["assignments"] if a["name"] == "Sect. 1.1 Matrices")["unlock_at"] = SEP_8
        saved = self.client.post(f"/api/canvas/content/{self.term}", json={"base_revision": view["revision"], "request_id": "copy-edit-3",
                                                                            "content": content}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        for course, assignment in ((self.course_a, self.wa_a), (self.course_b, self.wa_b)):
            self.fake.assignment(course, assignment)["unlock_at"] = sep_6
        self.nightly.round(NIGHT)
        self.assertNotIn("copy_due_dates", self.runs()[-1]["detail"])
        self.assertEqual(self.copy()[1]["unlock_at"], SEP_8)

    def test_the_morning_publishes_in_the_copy_what_canvas_has_published(self):
        """PUBLISHED IN CANVAS STANDS (docs/decisions/canvas.md; 2026-11-03: Quiz 3): the copy follows; nothing is written to Canvas."""
        self.start_copy()
        view, _ = self.copy()
        next(a for a in view["content"]["assignments"] if a["name"] == "Sect. 1.1 Matrices")["published"] = False
        saved = self.client.post(f"/api/canvas/content/{self.term}", json={"base_revision": view["revision"], "request_id": "copy-edit-4",
                                                                            "content": view["content"]}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        shown = self.client.post(f"/api/canvas/content/{self.term}/preview", json={"choices": {}}, headers=HEADERS).json
        pushed = self.client.post(f"/api/canvas/content/{self.term}/push", json={"fingerprint": shown["fingerprint"], "choices": shown["choices"],
                                                                                  "request_id": "copy-push-4", "confirmation": "push"}, headers=HEADERS)
        self.assertEqual(pushed.status_code, 200, pushed.json)
        for _ in range(200):
            if self.client.get(f"/api/canvas/content/{self.term}/pushes/{pushed.json['id']}").json["status"] != "running":
                break
            time.sleep(0.05)
        self.assertFalse(self.fake.assignment(self.course_a, self.wa_a)["published"])
        self.nightly.round(NIGHT)
        self.assertNotIn("copy_published", self.runs()[-1]["detail"])
        for course, assignment in ((self.course_a, self.wa_a), (self.course_b, self.wa_b)):
            self.fake.assignment(course, assignment).update(published=True, workflow_state="published")
        self.night(1)
        self.assertEqual(self.runs()[-1]["detail"]["copy_published"], 1)
        self.assertTrue(self.copy()[1]["published"])
        self.assertEqual(self.asked(), {})


def run_at(test):
    with connect(test.app.config["DATABASE"]) as db:
        return db.execute("SELECT created_at FROM gradebook_canvas_nightly_runs ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()[0]


class SyncTests(SyncBase):
    """Sync with Canvas never sends to WebAssign's assignments (requirement, 2026-10-06: Canvas always wins for WebAssign)."""

    def setUp(self):
        super().setUp()
        self.wa_a = self.fake.add_assignment(self.course_a, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN)
        self.fake.grade(self.course_a, self.wa_a, self.alice, 9)
        with patch.object(canvas_nightly, "HOUR", 0), patch.object(canvas_nightly, "END", 24):
            self.assertEqual(self.app.extensions["canvas_nightly"].round(), {self.term: "imported"})
        view = self.client.get(self.base).json
        self.items = {item["title"]: item["id"] for item in view["setup"]["items"]}

    def test_webassign_columns_are_never_in_a_sync_s_to_canvas_half(self):
        plan = self.preview()
        self.assertFalse([c for c in plan["columns"] if c["title"] == "Sect. 1.1 Matrices"])
        self.assertEqual([c["action"] for c in plan["from_canvas"]["changes"] if c["item_id"] == self.items["Sect. 1.1 Matrices"]], [])
        self.fake.grade(self.course_a, self.wa_a, self.bob, 4)
        plan, _ = self.sync()
        self.assertEqual([(c["name"], c["action"]) for c in plan["from_canvas"]["changes"] if c["item_id"] == self.items["Sect. 1.1 Matrices"]],
                         [("Example, Bob", "add")])
        self.assertFalse([w for w in self.fake.writes if f"/assignments/{self.wa_a}/" in w[1]])


if __name__ == "__main__":
    unittest.main()
