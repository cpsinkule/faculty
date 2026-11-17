"""Canvas (AGENTS.md "Canvas") offline: the kept session file, the client against the loopback FakeCanvas, each section's Canvas
course link and the roster comparison. Nothing here reaches Canvas; every session file is written in the test's own directory."""
import http.client
import io
import json
import os
import signal
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.exceptions import ServiceUnavailable

from browser_course_pages import seed_course
from fake_canvas import FakeCanvas
from test_rosters import STUDENTS, csv_bytes
from test_storage import HEADERS

from app import create_app
from canvas import stopping, web
from canvas.signin import CanvasBrowser
from rosters import connect

ALICE, BOB, CAROL = "001234567", "123456789", "222333444"


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeCanvas().start()
        self.addCleanup(self.fake.stop)
        self.file = os.path.join(self.temp.name, "config", "canvas-session.json")
        self.settings = web.Settings(session_file=self.file, base=self.fake.url)

    def keep(self):
        cookies = self.fake.session()
        web.save(self.settings, cookies, user={"name": "Pat Instructor"})
        return cookies


class WebTests(Base):
    def test_the_session_file_is_private_and_checked(self):
        self.assertEqual(self.settings.status()["signed_in"], False)
        with self.assertRaises(web.SignedOut):
            web.Client(self.settings)
        self.keep()
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(self.settings.status()["user"], {"name": "Pat Instructor"})
        os.chmod(self.file, 0o644)
        self.assertFalse(self.settings.status()["signed_in"])
        self.assertIn("0600", self.settings.status()["problem"])
        with self.assertRaises(web.CanvasError):
            web.Client(self.settings)
        os.chmod(self.file, 0o600)
        # A session kept for another Canvas host is not used.
        other = web.Settings(session_file=self.file, base="https://canvas.example.edu")
        with self.assertRaises(web.SignedOut):
            web.Client(other)
        self.assertFalse(web.Settings(session_file="relative.json").configured)
        self.assertTrue(web.forget(self.settings))
        self.assertFalse(os.path.exists(self.file))

    def test_only_https_or_loopback_canvas(self):
        self.assertEqual(web.base_url(""), web.BASE)
        self.assertEqual(web.base_url("http://127.0.0.1:9/"), "http://127.0.0.1:9")
        for bad in ("http://canvas.tamu.edu", "https://canvas.tamu.edu/courses", "https://user@canvas.tamu.edu"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                web.base_url(bad)

    def test_reads_follow_pages_strip_the_prefix_and_keep_renewed_cookies(self):
        cookies = self.keep()
        for n in range(105):
            self.fake.add_course(f"MATH 140 course {n}", f"MATH-140-{n}", "Fall 2026")
        with web.Client(self.settings) as canvas:
            self.assertEqual(canvas.me(), {"id": "7001", "name": "Pat Instructor"})
            self.assertEqual(len(canvas.courses()), 105)
        pages = [r for r in self.fake.requests if r["path"] == "/api/v1/courses"]
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(r["cookies"]["canvas_session"] == cookies["canvas_session"] for r in self.fake.requests))
        self.assertNotEqual(web.load(self.settings)["cookies"]["_csrf_token"], cookies["_csrf_token"])  # Canvas renewed it; it was kept

    def test_a_client_open_across_a_new_sign_in_never_writes_the_old_session_over_it(self):
        # A long push still holds the old sign-in's client when the instructor signs in again (Client.close).
        self.keep()
        old = web.Client(self.settings)
        old.me()  # Canvas renews the CSRF token on this client's answer
        self.assertTrue(old._renewed)
        fresh = self.keep()  # same user, same second: told apart by its own sign-in ID
        old.close()
        self.assertEqual(web.load(self.settings)["cookies"], fresh)
        # A client of the CURRENT sign-in keeps only what Canvas renewed for it; another client's renewal stays too.
        with web.Client(self.settings) as first, web.Client(self.settings) as second:
            first.me()
            second.me()
            first.cookies["canvas_session"] = "never written back"  # not renewed by Canvas, so never kept
        kept = web.load(self.settings)
        self.assertEqual(kept["cookies"]["canvas_session"], fresh["canvas_session"])
        self.assertEqual(kept["cookies"]["_csrf_token"], first.cookies["_csrf_token"])  # the last to close
        self.assertNotEqual(kept["cookies"]["_csrf_token"], fresh["_csrf_token"])

    def test_writes_send_the_csrf_token_and_a_refused_session_is_signed_out(self):
        self.keep()
        with web.Client(self.settings) as canvas:
            canvas.me()
            status, _, _ = canvas.request("POST", "/api/v1/nowhere", data={})
            self.assertEqual(status, 404)
        write = [r for r in self.fake.requests if r["method"] == "POST"][0]
        self.assertTrue(write["csrf"])
        self.fake.sessions.clear()
        with web.Client(self.settings) as canvas, self.assertRaises(web.SignedOut):
            canvas.me()

    def test_a_session_canvas_knows_but_refuses_is_not_signed_out(self):
        """Canvas also answers 401 to a session it knows that may not do something (a concluded course is read-only): signing in
        again changes nothing, so it is Canvas's refusal, not a signed-out session (2026-10-04 review)."""
        self.keep()
        self.fake.refuse.add(("POST", "/api/v1/nowhere"))
        with web.Client(self.settings) as canvas:
            with self.assertRaises(web.CanvasError) as caught:
                canvas.json("POST", "/api/v1/nowhere", data={})
            self.assertNotIsInstance(caught.exception, web.SignedOut)
            self.assertEqual(caught.exception.status, 401)
            self.assertIn("not authorized", str(caught.exception))
            # A file Canvas refuses answers 401 without saying why: Canvas is asked who the session is, and it still knows it.
            with self.assertRaises(web.CanvasError) as caught:
                canvas.download(f"{self.fake.url}/files/nothing-here/download", 1000)
            self.assertNotIsInstance(caught.exception, web.SignedOut)
        self.fake.sessions.clear()
        with web.Client(self.settings) as canvas, self.assertRaises(web.SignedOut):
            canvas.download(f"{self.fake.url}/files/nothing-here/download", 1000)

    def test_a_kept_session_that_cannot_be_written_never_fails_what_canvas_did(self):
        self.keep()
        with patch("canvas.web.save", side_effect=OSError("No space left on device")):
            with web.Client(self.settings) as canvas:
                self.assertEqual(canvas.me()["name"], "Pat Instructor")  # Canvas renewed a cookie; keeping it failed quietly

    def test_a_link_to_another_host_is_never_followed(self):
        self.keep()
        with web.Client(self.settings) as canvas, self.assertRaisesRegex(web.CanvasError, "somewhere else"):
            canvas._path("https://evil.example/api/v1/courses?page=2")

    def test_attachment_address_is_checked_before_its_first_request(self):
        """CANVAS: the initial file address obeys the same https and no-credentials rule as each redirect."""
        self.keep()
        with web.Client(self.settings) as canvas, patch.object(canvas, "request") as request, \
                patch.object(canvas, "_fetch_elsewhere") as elsewhere:
            for address in ("http://files.example.test/paper.pdf", "https://name:password@files.example.test/paper.pdf",
                            "file:///paper.pdf", "https:///paper.pdf"):
                with self.subTest(address=address), self.assertRaises(web.CanvasError):
                    canvas.download(address, 1024)
            request.assert_not_called()
            elsewhere.assert_not_called()

    def test_a_write_canvas_refused_is_told_from_one_that_may_have_landed(self):
        """CANVAS, OUTSIDE SERVICES: only a 4xx answer means the write did not take; no answer or a server error is uncertain."""
        for status, refused in ((399, False), (400, True), (401, True), (499, True), (500, False), (0, False), (None, False)):
            with self.subTest(status=status):
                self.assertIs(web.CanvasError("Canvas answered.", status=status).refused, refused)
        self.assertIs(web.CanvasError("Canvas could not be reached.").refused, False)
        self.assertIs(web.SignedOut("Sign in to Canvas again.").refused, False)  # no status: never a refused write

    def test_a_file_service_gets_nothing_of_the_session_and_the_asked_for_connection(self):
        """CANVAS: a download's GET and an upload's POST outside Canvas's host carry no cookie or header of the session, over https
        (a loopback test server excepted), with their own timeout and read limit; a lost answer reads the same for both."""
        self.keep()
        response = Mock(status=200)
        response.getheaders.return_value = [("Content-Type", "text/plain"), ("X-Thing", "1")]
        response.read.return_value = b"bytes"

        def sent(connection):
            (method, path), kwargs = connection.request.call_args
            return method, path, kwargs.get("body"), kwargs["headers"]
        secure = urllib.parse.urlsplit("https://files.example.test/a/b.pdf?token=1")
        local = urllib.parse.urlsplit("http://127.0.0.1:9/up")
        with web.Client(self.settings) as canvas, patch("http.client.HTTPSConnection") as https, \
                patch("http.client.HTTPConnection") as plain:
            https.return_value.getresponse.return_value = plain.return_value.getresponse.return_value = response
            self.assertEqual(canvas._fetch_elsewhere(secure, 100), (200, {"content-type": "text/plain", "x-thing": "1"}, b"bytes"))
            https.assert_called_once_with("files.example.test", timeout=web.TIMEOUT)
            plain.assert_not_called()
            self.assertEqual(sent(https.return_value), ("GET", "/a/b.pdf?token=1", None, {"User-Agent": "Faculty"}))
            response.read.assert_called_once_with(101)
            https.return_value.close.assert_called_once_with()
            response.read.reset_mock()
            self.assertEqual(canvas._post_elsewhere(local, b"payload", "multipart/form-data; boundary=x"),
                             (200, {"content-type": "text/plain", "x-thing": "1"}, b"bytes"))
            plain.assert_called_once_with("127.0.0.1:9", timeout=web.TIMEOUT * 3)
            self.assertEqual(sent(plain.return_value), ("POST", "/up", b"payload", {
                "User-Agent": "Faculty", "Content-Type": "multipart/form-data; boundary=x", "Content-Length": "7"}))
            response.read.assert_called_once_with(web.LIMIT + 1)
            plain.return_value.close.assert_called_once_with()
            self.assertEqual(https.call_count, 1)
            # A lost request or answer is the same CanvasError for both, and the connection is closed.
            for lost, name in ((TimeoutError("slow"), "TimeoutError"), (http.client.BadStatusLine("x"), "BadStatusLine")):
                for where in ("request", "getresponse"):
                    for call in (lambda: canvas._fetch_elsewhere(secure, 100), lambda: canvas._post_elsewhere(secure, b"x", "text/plain")):
                        with self.subTest(lost=name, where=where):
                            connection = https.return_value
                            connection.reset_mock()
                            getattr(connection, where).side_effect = lost
                            with self.assertRaises(web.CanvasError) as caught:
                                call()
                            self.assertEqual(str(caught.exception), f"Canvas's file service could not be reached ({name}).")
                            self.assertIsNone(caught.exception.status)
                            self.assertIsNone(caught.exception.__cause__)
                            connection.close.assert_called_once_with()
                            getattr(connection, where).side_effect = None

    def test_an_upload_address_obeys_the_same_rule_as_a_download(self):
        """CANVAS: the file service an upload is sent to is https without credentials (a loopback test server excepted when Canvas
        itself is one), as a download's address is."""
        self.keep()
        with web.Client(self.settings) as canvas, patch.object(canvas, "request") as request, \
                patch.object(canvas, "_post_elsewhere") as post:
            for address in ("http://files.example.test/up", "https://name:password@files.example.test/up", "file:///up", "https:///up"):
                request.return_value = 200, {}, json.dumps({"upload_url": address, "upload_params": {}}).encode()
                with self.subTest(address=address), self.assertRaisesRegex(web.CanvasError, "does not send files") as caught:
                    canvas.upload_message_file("paper.pdf", "application/pdf", b"x")
                self.assertEqual(caught.exception.status, 200)
            post.assert_not_called()
            for address in ("https://files.example.test/up", "http://127.0.0.1:9/up"):
                post.reset_mock()
                request.return_value = 200, {}, json.dumps({"upload_url": address, "upload_params": {}}).encode()
                post.return_value = 200, {}, b'{"id": 5}'
                with self.subTest(address=address):
                    self.assertEqual(canvas.upload_message_file("paper.pdf", "application/pdf", b"x"), "5")
                    self.assertEqual(post.call_args.args[0], urllib.parse.urlsplit(address))
            # A download is refused the same, before anything is sent.
            for address in ("http://files.example.test/up", "https://name:password@files.example.test/up", "file:///up", "https:///up"):
                with self.subTest(address=address), self.assertRaisesRegex(web.CanvasError, "does not fetch from"):
                    canvas.download(address, 100)


class Seeded(Base):
    def setUp(self):
        super().setUp()
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        self.service = self.app.extensions["canvas"]
        self.service.settings = self.settings
        seed_course(self.client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = self.client.get("/api/state").json["course_groups"][0]
        rows = [*STUDENTS, ["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@example.test"]]
        for number, chosen in ((507, rows[:2]), (508, rows[2:])):
            route = f"/api/course-groups/{group['id']}/sections/{number}/roster"
            answer = self.client.post(route, data={"file": (io.BytesIO(csv_bytes(chosen)), "roster.csv")}, headers=HEADERS)
            self.assertEqual(answer.status_code, 201, answer.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}
        self.route = f"/api/canvas/sections/{self.sections[507]}"


class ServiceTests(Seeded):

    def test_targeted_grade_read_selects_its_assignments_once(self):
        """PERFORMANCE: retain the selection used for the read instead of rebuilding the column map for its receipt."""
        self.keep()
        course = self.fake.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        assignment = self.fake.add_assignment(course, "Quiz 1", 10)
        calls = []

        def wanted(course_id, assignments):
            calls.append(course_id)
            return {assignment} if len(calls) == 1 else set()

        found = self.app.extensions["canvas"].read_for_send({course}, wanted)
        self.assertEqual(calls, [course])
        self.assertEqual(found[course]["read"], [assignment])

    def test_reading_the_link_never_contacts_canvas_and_signing_out_is_a_401(self):
        link = self.client.get(self.route).json
        self.assertEqual((link["section"], link["term_label"], link["linked"], link["signed_in"]), (507, "Fall 2026", None, False))
        self.assertEqual(self.client.get("/api/canvas/status").json["signed_in"], False)
        self.assertEqual(self.fake.requests, [])
        answer = self.client.get(self.route + "/courses")
        self.assertEqual(answer.status_code, 401)
        self.assertIn("Sign in to Canvas", answer.json["error"])
        self.keep()
        self.fake.sessions.clear()  # Canvas no longer accepts the kept session
        self.assertEqual(self.client.get(self.route + "/courses").status_code, 401)
        self.assertEqual(self.client.post("/api/canvas/check", json={}, headers=HEADERS).json["valid"], False)

    def test_a_refusal_canvas_gives_a_known_session_is_not_a_sign_in(self):
        self.keep()
        self.fake.refuse.add(("GET", "/api/v1/courses"))
        answer = self.client.get(self.route + "/courses")
        self.assertEqual(answer.status_code, 502)  # the page says what Canvas said; it does not open the sign-in window
        self.assertIn("not authorized", answer.json["error"])

    def test_a_section_links_one_of_its_canvas_courses_with_a_proposal_and_a_stale_check(self):
        self.keep()
        mine = self.fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026C", "Fall 2026")
        self.fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026A", "Spring 2026")
        other = self.fake.add_course("MATH-140-508: Mathematics for Business", "MATH-140-508-2026C", "Fall 2026")
        listed = self.client.get(self.route + "/courses").json
        self.assertEqual((listed["proposed"], len(listed["courses"])), (mine, 3))
        self.assertEqual(listed["courses"][0]["term"], "Fall 2026")  # this term's courses first
        for body, status in (({"course_id": "x"}, 400), ({"course_id": "99999", "base": ""}, 409), ({"course_id": other, "base": "1"}, 409)):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.route, json=body, headers=HEADERS).status_code, status)
        saved = self.client.post(self.route, json={"course_id": mine, "base": ""}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["linked"]["course_id"], mine)
        self.assertEqual(self.client.post(self.route, json={"course_id": other, "base": ""}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.post(self.route, json={"course_id": other, "base": mine}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.post(self.route, json={"course_id": other, "base": other}, headers=HEADERS).status_code, 200)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT course_id FROM canvas_course_links ORDER BY created_at,rowid")], [mine, other])
            with self.assertRaisesRegex(Exception, "append-only"):
                db.execute("UPDATE canvas_course_links SET course_id='1'")
        self.assertEqual(self.client.post(self.route, json={"course_id": mine, "base": other}).status_code, 403)  # the workspace guard

    def test_nothing_is_proposed_when_two_courses_fit(self):
        self.keep()
        self.fake.add_course("MATH 140-507", "MATH-140-507", "Fall 2026")
        self.fake.add_course("MATH 140-507 (combined)", "MATH-140-507-508", "Fall 2026")
        self.assertEqual(self.client.get(self.route + "/courses").json["proposed"], "")

    def test_the_roster_comparison_matches_uins_exactly_and_never_returns_one(self):
        self.keep()
        course = self.fake.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        section = self.fake.add_section(course, "MATH-140-507")
        self.fake.add_student(course, section, "Example, Alice", ALICE)
        self.fake.add_student(course, section, "Example, Carol", CAROL)            # on Faculty's roster of Section 508
        self.fake.add_student(course, section, "Newcomer, Dana", "999888777")      # on no roster here
        self.fake.add_student(course, section, "Guest, Eli", "")                   # no university ID
        self.fake.add_student(course, section, "Dropped, Finn", "555444333", state="deleted")
        self.assertEqual(self.client.get(self.route + "/roster").status_code, 409)  # not linked yet
        self.assertEqual(self.client.post(self.route, json={"course_id": course, "base": ""}, headers=HEADERS).status_code, 200)
        answer = self.client.get(self.route + "/roster")
        self.assertEqual(answer.status_code, 200, answer.json)
        found = answer.json
        self.assertEqual(found["counts"], {"faculty": 2, "canvas": 4, "both": 1, "only_faculty": 1, "only_canvas": 1, "other_section": 1, "no_id": 1,
                                           "dropped": 0, "dropped_in_canvas": 0})
        self.assertEqual([r["name"] for r in found["only_faculty"]], ["Example, Bob"])
        self.assertEqual([r["name"] for r in found["only_canvas"]], ["Newcomer, Dana"])
        self.assertEqual(found["other_section"][0]["faculty_section"], 508)
        self.assertEqual([r["name"] for r in found["no_id"]], ["Guest, Eli"])
        text = json.dumps(found)
        for uin in (ALICE, BOB, CAROL, "999888777"):
            self.assertNotIn(uin, text)

    def test_a_student_canvas_no_longer_lists_can_be_dropped_and_restored(self):
        """requirement, 2026-09-27: drop a student Canvas no longer lists, to avoid future conflicts. A recorded, reversible mark."""
        self.keep()
        course = self.fake.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        section = self.fake.add_section(course, "MATH-140-507")
        alice = self.fake.add_student(course, section, "Example, Alice", ALICE)
        self.client.post(self.route, json={"course_id": course, "base": ""}, headers=HEADERS)
        bob = self.client.get(self.route + "/roster").json["only_faculty"][0]
        group = self.client.get("/api/state").json["course_groups"][0]
        roster = f"/api/course-groups/{group['id']}/sections/507/roster"
        request = {"enrollment_ids": [bob["enrollment_id"]], "request_id": "drop-1", "source": "canvas", "reason": "Not in Canvas"}
        self.assertEqual(self.client.post(roster + "/drop", json=request).status_code, 403)  # the workspace guard
        self.assertEqual(self.client.post(roster + "/drop", json={**request, "enrollment_ids": ["1234567890"]}, headers=HEADERS).status_code, 400)
        dropped = self.client.post(roster + "/drop", json=request, headers=HEADERS)
        self.assertEqual(dropped.status_code, 200, dropped.json)
        self.assertEqual((dropped.json["changed"], dropped.json["count"], dropped.json["dropped_count"]), (1, 1, 1))
        marked = next(s for s in dropped.json["students"] if s["id"] == bob["enrollment_id"])
        self.assertEqual((marked["dropped"]["source"], marked["dropped"]["reason"]), ("canvas", "Not in Canvas"))
        self.assertEqual(self.client.post(roster + "/drop", json=request, headers=HEADERS).json["changed"], 0)  # the same request again
        # Current work no longer sees the student; the membership and its records stay.
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM roster_current WHERE section_id=?", (self.sections[507],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM roster_memberships WHERE section_id=?", (self.sections[507],)).fetchone()[0], 2)
            with self.assertRaisesRegex(Exception, "append-only"):
                db.execute("UPDATE roster_drops SET reason='x'")
        summary = self.client.get("/api/course-groups/summary").json["groups"][group["id"]]["sections"]["507"]
        self.assertEqual(summary["students"], 1)
        compared = self.client.get(self.route + "/roster").json
        self.assertEqual((compared["counts"]["faculty"], compared["counts"]["dropped"], compared["only_faculty"]), (1, 1, []))
        # A later roster import that still lists them leaves them dropped and says so.
        imported = self.client.post(roster, data={"file": (io.BytesIO(csv_bytes(STUDENTS)), "roster.csv")}, headers=HEADERS)
        self.assertEqual((imported.status_code, imported.json["last_import"]["still_dropped"], imported.json["dropped_count"]), (201, 1, 1))
        # Canvas lists them again: the comparison offers them back, and Restore makes them current.
        self.fake.add_student(course, section, "Example, Bob", BOB)
        compared = self.client.get(self.route + "/roster").json
        self.assertEqual([r["name"] for r in compared["dropped_in_canvas"]], ["Example, Bob"])
        restored = self.client.post(roster + "/restore", json={"enrollment_ids": [bob["enrollment_id"]], "request_id": "restore-1", "source": "canvas"},
                                    headers=HEADERS)
        self.assertEqual((restored.json["changed"], restored.json["count"], restored.json["dropped_count"]), (1, 2, 0))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT action FROM roster_drops ORDER BY rowid")], ["drop", "restore"])
        self.assertTrue(alice)


class GradebookCanvasBase(Seeded):
    """Two linked Canvas courses with graded assignments and a term gradebook (shared by the import and send tests)."""

    def setUp(self):
        super().setUp()
        self.keep()
        f = self.fake
        self.course_a = f.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        self.course_b = f.add_course("MATH-140-508", "MATH-140-508", "Fall 2026")
        sa, sb = f.add_section(self.course_a, "507"), f.add_section(self.course_b, "508")
        self.alice, self.bob = f.add_student(self.course_a, sa, "Example, Alice", ALICE), f.add_student(self.course_a, sa, "Example, Bob", BOB)
        self.carol = f.add_student(self.course_b, sb, "Example, Carol", CAROL)
        f.add_student(self.course_a, sa, "Newcomer, Dana", "999888777")
        self.quiz_a = f.add_assignment(self.course_a, "Quiz 1", 10, group="Quizzes")
        self.test_a = f.add_assignment(self.course_a, "Test 1", 100, group="Exams", due_at="2026-10-02T04:59:59Z")
        self.hw_a = f.add_assignment(self.course_a, "Homework 2", 5, group="Homework")
        self.quiz_b = f.add_assignment(self.course_b, "Quiz 1", 10, group="Quizzes")
        self.hw_b = f.add_assignment(self.course_b, "Homework 2", 5, group="Homework")
        self.empty_b = f.add_assignment(self.course_b, "Reading check", 2)
        f.add_assignment(self.course_b, "Attendance note", 0, grading_type="not_graded")
        # Every assignment posts grades by hand, as the live courses do (POSTING GRADES: a by-hand column's automatic assignment is
        # switched before its grades go; the switch tests make theirs automatic).
        for assignment in (a for groups in f.groups.values() for g in groups for a in g["assignments"]):
            assignment["post_manually"] = True
        f.course_manual = {self.course_a: True, self.course_b: True}
        f.grade(self.course_a, self.quiz_a, self.alice, 9)
        f.grade(self.course_a, self.quiz_a, self.bob, 8)
        f.grade(self.course_a, self.test_a, self.alice, 88.123456, posted=False)
        f.grade(self.course_a, self.test_a, self.bob, 50, state="pending_review")
        f.grade(self.course_a, self.hw_a, self.alice, 5)
        f.grade(self.course_a, self.hw_a, self.bob, excused=True)
        f.grade(self.course_b, self.quiz_b, self.carol, 6)
        f.grade(self.course_b, self.hw_b, self.carol, missing=True)
        for number, course in ((507, self.course_a), (508, self.course_b)):
            answer = self.client.post(f"/api/canvas/sections/{self.sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS)
            self.assertEqual(answer.status_code, 200, answer.json)
        self.term = self.client.get("/api/gradebook").json["terms"][0]["id"]
        self.base = f"/api/gradebook/terms/{self.term}"
        saved = self.client.post(self.base + "/setup", json={"base_revision": None, "request_id": "setup-1", "setup": {
            "categories": [{"ref": "q", "name": "Quizzes"}],
            "items": [{"title": "Quiz 1", "points": "10", "category_id": "q"}, {"title": "Exam 1", "points": "100"}]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        view = saved.json
        self.items = {item["title"]: item["id"] for item in view["setup"]["items"]}
        self.rows = {student["name"]: student["row"] for student in view["students"]}
        typed = self.client.post(self.base + "/scores", json={"request_id": "typed-1", "changes": [
            {"item_id": self.items["Quiz 1"], "row": self.rows["Example, Bob"], "base": None, "status": "scored", "points": "7"}]}, headers=HEADERS)
        self.assertEqual(typed.status_code, 200, typed.json)

    def preview(self, options=None, courses=None, status=200, **extra):
        """Sync with Canvas's summary (docs/decisions/canvas.md SYNC WITH CANVAS)."""
        answer = self.client.post(self.base + "/canvas-sync/preview", json={"options": options or {}, **({"courses": courses} if courses else {})},
                                  headers=HEADERS, **extra)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def apply(self, plan, request_id, held=None):
        """The instructor's Sync of that summary: its options, ticked courses and fingerprint (request IDs of 8 characters or more)."""
        return self.client.post(self.base + "/canvas-sync", json={"options": plan["options"], "fingerprint": plan["fingerprint"],
                                                                   "request_id": request_id, "confirmation": "sync",
                                                                   **({"courses": plan["ticked"]} if plan.get("ticked") else {}),
                                                                   **({"held": held} if held else {})}, headers=HEADERS)

    def ended(self, answer):
        """A sync followed until its send ends (however it ends): the send's last view, or None when it sent nothing."""
        self.assertEqual(answer.status_code, 200, answer.json)
        sent = answer.json["send"]
        for _ in range(200):
            if not sent or sent["status"] != "running":
                break
            time.sleep(0.05)
            sent = self.client.get(f"{self.base}/canvas-send/{sent['id']}").json
        return sent

    def sync(self, options=None, held=None, courses=None, request_id=None):
        """Summary, then Sync, followed to the end; the send must finish done when it sent anything."""
        plan = self.preview(options, courses)
        sent = self.ended(self.apply(plan, request_id or f"sync-{time.monotonic_ns()}", held))
        if sent:
            self.assertEqual(sent["status"], "done", sent)
        return plan, sent


class StopTests(unittest.TestCase):
    """canvas/stopping.py: a restart waits for the writes to Canvas that are running (2026-10-04 review)."""

    def test_systemd_lets_the_stop_wait_for_a_whole_sync(self):
        """2026-10-06: systemd's default 90 s capped the wait at 80, and a restart cut a Sync with Canvas off with 4 of 20 unsent."""
        conf = (Path(__file__).resolve().parents[1] / "deploy" / "faculty.service.d" / "stop-wait.conf").read_text(encoding="utf-8")
        allowed = int(next(line.split("=", 1)[1] for line in conf.splitlines() if line.startswith("TimeoutStopSec=")))
        self.assertGreaterEqual(stopping.STOP_WAIT, 600)
        self.assertGreater(allowed, stopping.STOP_WAIT + stopping.ANSWER_WAIT)

    def test_a_stop_refuses_new_writes_waits_for_running_ones_then_ends_through_the_handler_it_replaced(self):
        writes = stopping.Writes()
        writes.begin("push:1", "a push to Canvas", "Faculty is restarting.")
        previous, stopped = Mock(), []
        with patch("canvas.stopping.os.kill", side_effect=lambda pid, signum: stopped.append(signum)), \
             patch("canvas.stopping.signal.signal") as installed:
            began = time.monotonic()
            stopping.stop_handler(writes, timeout=5, previous=previous)(signal.SIGTERM, None)
            self.assertLess(time.monotonic() - began, 1)  # the handler returns: waitress goes on answering
            installed.assert_called_once_with(signal.SIGTERM, previous)
            with self.assertRaisesRegex(ServiceUnavailable, "restarting"):
                writes.begin("send:2", "grades sent to Canvas", "Faculty is restarting.")
            time.sleep(0.2)
            self.assertEqual(stopped, [])
            writes.end("push:1")
            until = time.monotonic() + 5
            while not stopped and time.monotonic() < until:
                time.sleep(0.02)
        self.assertEqual(stopped, [signal.SIGTERM])
        # With no handler of its own before it, the default action ends the process, at once when nothing runs.
        stopped.clear()
        with patch("canvas.stopping.os.kill", side_effect=lambda pid, signum: stopped.append(signum)), \
             patch("canvas.stopping.signal.signal") as installed:
            stopping.stop_handler(stopping.Writes(), previous=signal.SIG_DFL)(signal.SIGTERM, None)
            installed.assert_called_once_with(signal.SIGTERM, signal.SIG_DFL)
            until = time.monotonic() + 5
            while not stopped and time.monotonic() < until:
                time.sleep(0.02)
        self.assertEqual(stopped, [signal.SIGTERM])


class SignInTests(unittest.TestCase):
    def test_canvas_pages_are_the_target_and_tamu_s_steps_are_aim_s(self):
        settings = web.Settings(session_file="/x/canvas.json", base="https://canvas.tamu.edu")
        browser = CanvasBrowser.__new__(type("B", (CanvasBrowser,), {"SERVICE": type("S", (), {"settings": settings})()}))
        classify = browser._classify
        self.assertEqual(classify("https://canvas.tamu.edu/", "Dashboard", []), "canvas")
        self.assertEqual(classify("https://canvas.tamu.edu/login/saml", "", []), "working")
        self.assertEqual(classify("https://login.microsoftonline.com/x", "Sign in", [{"type": "email", "name": "loginfmt"}]), "netid")
        self.assertEqual(classify("https://api-1.duosecurity.com/frame", "Enter code in Duo Mobile\n482\n", []), "duo_code")
        self.assertEqual(classify("https://canvas.example.edu/", "Dashboard", []), "unknown")  # another Canvas is not this one


if __name__ == "__main__":
    unittest.main()
