"""iClicker (AGENTS.md "iClicker") offline: the saved sign-in and the client against the loopback FakeIClicker, a section's iClicker
course, and the gradebook's Import from iClicker (gradebook/README.md). Nothing here reaches iClicker; every sign-in file is written
in the test's own directory, and the fake records every request so the tests can check that Faculty only reads."""
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from werkzeug.exceptions import BadGateway

from browser_course_pages import seed_course
from fake_iclicker import EMAIL, PASSWORD, FakeIClicker
from test_rosters import STUDENTS, csv_bytes
from test_storage import HEADERS

from app import create_app
from gradebook import iclicker_import
from iclicker import service as iclicker_service
from iclicker import web
from gradebook import iclicker_after_class
from rosters import connect

ALICE, BOB, CAROL = "001234567", "123456789", "222333444"


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeIClicker().start()
        self.addCleanup(self.fake.stop)
        web.forget_tokens()
        self.addCleanup(web.forget_tokens)
        self.file = os.path.join(self.temp.name, "config", "sign-in.json")
        os.makedirs(os.path.dirname(self.file), mode=0o700)
        self.write_sign_in(EMAIL, PASSWORD)
        self.settings = web.Settings.testing(self.file, self.fake.url)

    def write_sign_in(self, email, password, mode=0o600):
        with open(self.file, "w", encoding="utf-8") as handle:
            json.dump({"email": email, "password": password}, handle)
        os.chmod(self.file, mode)


class WebTests(Base):
    def test_one_sign_in_serves_every_read_and_nothing_is_written(self):
        course = self.fake.add_course("Math-140-507-Fall-26")
        self.fake.add_course("Office hours", kind="EVENT")
        alice = self.fake.add_student(course, "Alice", "Example", ALICE, "alice@example.test")
        first = self.fake.add_session(course, "Session 1", "2026-09-01T15:00:00.000Z")
        for n in range(2, 4):
            self.fake.add_session(course, f"Session {n}", f"2026-09-0{n}T15:00:00.000Z")
        self.fake.score(course, first, alice, 1.0, 1.0)
        self.fake.page_size = 1
        with web.Client(self.settings) as client:
            courses = client.courses()
            roster = client.roster(course)
            sessions = client.sessions(course)
        with web.Client(self.settings) as client:
            client.courses()
        self.assertEqual(self.fake.logins, 1)  # the token is kept in memory and used again
        self.assertEqual([c["name"] for c in courses], ["Math-140-507-Fall-26"])  # events are not courses
        self.assertEqual((courses[0]["term"], courses[0]["archived"]), ("Fall 2026", False))
        self.assertEqual((roster[0]["student_id"], roster[0]["email"], roster[0]["removed"]), (ALICE, "alice@example.test", False))
        self.assertEqual([s["name"] for s in sessions], ["Session 1", "Session 2", "Session 3"])  # every page
        self.assertEqual(sessions[0]["users"][0]["total"], 2.0)
        self.assertEqual({m for m, path in self.fake.requests if path != "/authproxy/login"}, {"GET"})
        self.assertEqual([path for m, path in self.fake.requests if m == "POST"], ["/authproxy/login"])

    def test_a_refused_token_signs_in_once_more(self):
        self.fake.add_course("Math-140-507-Fall-26")
        with web.Client(self.settings) as client:
            client.courses()
            self.fake.tokens.clear()
            self.assertEqual(len(client.courses()), 1)
        self.assertEqual(self.fake.logins, 2)

    def test_unreadable_lists_are_not_reported_as_empty_courses_rosters_or_sessions(self):
        """ICLICKER: a malformed remote read must fail, never mark an after-class read complete with no sessions."""
        with web.Client(self.settings) as client:
            client.token, client.user_id = "test-token", "test-user"
            for read, response in ((client.courses, {}), (lambda: client.roster("test-course"), {}),
                                   (lambda: client.roster("test-course"), {"rosterEntries": None}),
                                   (lambda: client.sessions("test-course"), {})):
                with self.subTest(response=response), patch.object(client, "get", return_value=(response, {})):
                    with self.assertRaises(web.IClickerError):
                        read()
            with patch.object(client, "get", return_value=([], {"has-more": "true"})):
                with self.assertRaisesRegex(web.IClickerError, "empty session page"):
                    client.sessions("test-course")
            for read, response in ((client.courses, []), (lambda: client.roster("test-course"), {"rosterEntries": []}),
                                   (lambda: client.sessions("test-course"), [])):
                with self.subTest(empty=read), patch.object(client, "get", return_value=(response, {})):
                    self.assertEqual(read(), [])

    def test_a_refused_sign_in_names_no_secret(self):
        self.write_sign_in(EMAIL, "not the password")
        with self.assertRaises(web.SignInRefused) as caught, web.Client(self.settings) as client:
            client.courses()
        self.assertNotIn("not the password", str(caught.exception))
        self.assertIn("save-gradescope.py", str(caught.exception))

    def test_the_sign_in_file_is_checked(self):
        os.chmod(self.file, 0o644)
        self.assertIn("0600", self.settings.status()["problem"])
        with self.assertRaises(web.NotSetUp):
            web.Client(self.settings)
        os.unlink(self.file)
        self.assertIn("missing", self.settings.status()["problem"])
        self.assertFalse(web.Settings().configured)
        self.assertEqual(web.Settings().status()["configured"], False)

    def test_only_iclicker_or_a_loopback_test_server(self):
        with self.assertRaises(ValueError):
            web.Settings.testing(self.file, "http://example.com")
        with patch.dict(os.environ, {web.URL_ENV: "https://api.example.com"}), self.assertRaises(ValueError):
            web.Settings.environment()
        with patch.dict(os.environ, {web.CREDENTIALS_ENV: self.file}, clear=False):
            os.environ.pop(web.URL_ENV, None)
            live = web.Settings.environment()
        self.assertEqual((live.gateway, live.activity), (web.GATEWAY, web.ACTIVITY))


class ProposeTests(unittest.TestCase):
    SECTION = {"section": 507, "code": "MATH 140", "term_label": "Fall 2026"}

    def course(self, name, term="", archived=False):
        return {"id": name.replace(" ", "-")[:60], "name": name, "term": term, "start": "", "end": "", "archived": archived}

    def test_the_one_current_course_with_the_section_and_course_numbers(self):
        courses = [self.course("Math-140-507-Fall-26", "Fall 2026"), self.course("Math-140-508-Fall26", "Fall 2026"),
                   self.course("26 SPRING MATH 142 510: BUSINESS CALCULUS", "", archived=True)]
        self.assertEqual(iclicker_service.propose(courses, self.SECTION)["name"], "Math-140-507-Fall-26")
        # Without a term, the season and year in the name decide; another term's course is not proposed.
        self.assertEqual(iclicker_service.propose([self.course("Math-140-507-Fall-26")], self.SECTION)["name"], "Math-140-507-Fall-26")
        self.assertIsNone(iclicker_service.propose([self.course("Math-140-507-Spring-26")], self.SECTION))
        self.assertIsNone(iclicker_service.propose([self.course("Math-140-507-Fall-26", archived=True)], self.SECTION))
        # Two that fit: nothing is guessed.
        self.assertIsNone(iclicker_service.propose([self.course("Math-140-507-Fall-26", "Fall 2026"), self.course("MATH 140 507 copy", "Fall 2026")], self.SECTION))


class MailboxTests(unittest.TestCase):
    """One TAMU mailbox, two domains (requirement, 2026-10-02): the roster says "@email.tamu.edu", iClicker "@tamu.edu"."""

    def matched(self, emails, term_students):
        roster = [{"id": f"u{n}", "first": "Carson", "last": "Example", "email": email, "student_id": "", "removed": False, "blocked": False}
                  for n, email in enumerate(emails, 1)]
        data, facts = iclicker_import.shape({"c1": {"roster": roster, "sessions": []}}, {}, term_students)
        return {student["user_id"]: student["sis"] for student in data["c1"]["students"]}, facts

    def test_email_tamu_edu_and_tamu_edu_are_one_address(self):
        found, facts = self.matched(["Carson.Example@tamu.edu"], [{"uin": ALICE, "email": "carson.example@email.tamu.edu"}])
        self.assertEqual((found, facts["by_email"], facts["unmatched"]), ({"u1": ALICE}, 1, []))
        found, _ = self.matched(["carson.example@email.tamu.edu"], [{"uin": ALICE, "email": "carson.example@tamu.edu"}])
        self.assertEqual(found, {"u1": ALICE})

    def test_one_mailbox_on_two_roster_students_is_never_guessed(self):
        found, facts = self.matched(["x@tamu.edu"], [{"uin": ALICE, "email": "x@tamu.edu"}, {"uin": BOB, "email": "x@email.tamu.edu"}])
        self.assertEqual((found, len(facts["unmatched"])), ({"u1": ""}, 1))

    def test_other_domains_match_only_exactly(self):
        found, _ = self.matched(["x@tamu.edu", "y@gmail.com"], [{"uin": ALICE, "email": "x@gmail.com"}, {"uin": BOB, "email": "y@email.gmail.com"}])
        self.assertEqual(found, {"u1": "", "u2": ""})


class Seeded(Base):
    def setUp(self):
        super().setUp()
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        self.service = self.app.extensions["iclicker"]
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
        f = self.fake
        self.course_a = f.add_course("Math-140-507-Fall-26")
        self.course_b = f.add_course("Math-140-508-Fall26")
        f.add_course("26 SPRING MATH 142 510: BUSINESS CALCULUS-Sinkule", term=None, archived=True)
        self.alice = f.add_student(self.course_a, "Alice", "Example", ALICE, "alice@example.test")
        self.bob = f.add_student(self.course_a, "Bob", "Example", "bobby", "BOB@example.test")  # no UIN: matched by roster email
        f.add_student(self.course_a, "Dana", "Newcomer", "", "dana@example.test")                # on no roster here, no UIN
        f.add_student(self.course_a, "Eve", "Gone", "", "eve@example.test", removed=True)
        self.carol = f.add_student(self.course_b, "Carol", "Example", CAROL, "carol@example.test")
        self.s1 = f.add_session(self.course_a, "Session 1", "2026-09-01T15:00:00.000Z")
        self.s2 = f.add_session(self.course_a, "Session 2", "2026-09-03T15:00:00.000Z")
        f.add_session(self.course_a, "Exit poll", "2026-09-03T15:40:00.000Z", kind="EXIT_POLL")
        # Only polls are imported (requirement, 2026-10-06): a quiz and an attendance check are not.
        f.add_session(self.course_a, "Quiz", "2026-09-03T15:45:00.000Z", kind="QUIZ")
        f.add_session(self.course_a, "Attendance", "2026-09-03T14:55:00.000Z", kind="ATTENDANCE")
        f.add_session(self.course_a, "Session 3", "2026-09-05T15:00:00.000Z", ended=False)
        f.score(self.course_a, self.s1, self.alice, 1.0, 1.0)
        f.score(self.course_a, self.s1, self.bob, 1.0, 0.0, by_enrollment=True)
        f.score(self.course_a, self.s2, self.alice, 1.0, 1.0)
        self.b1 = f.add_session(self.course_b, "Session 1", "2026-09-02T15:00:00.000Z")
        f.score(self.course_b, self.b1, self.carol, 1.0, 1.0)
        self.term = self.client.get("/api/gradebook").json["terms"][0]["id"]
        self.base = f"/api/gradebook/terms/{self.term}"

    def link(self, number, course, base=""):
        return self.client.post(f"/api/iclicker/sections/{self.sections[number]}", json={"course_id": course, "base": base}, headers=HEADERS)

    def preview(self, number, options=None):
        answer = self.client.post(self.base + "/iclicker/preview", json={"options": options or {}, "section_id": self.sections[number]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def apply(self, number, plan, request_id):
        return self.client.post(self.base + "/iclicker", json={"options": plan["options"], "fingerprint": plan["fingerprint"],
                                                                "request_id": request_id, "section_id": self.sections[number]}, headers=HEADERS)


class SectionCourseTests(Seeded):
    def test_the_view_offers_iclicker_where_it_is_set_up(self):
        self.assertEqual(self.client.get(self.base).json["iclicker"], {"linked_sections": [], "last_import": None, "after_class": {}, "scheduled": False})
        self.service.settings = web.Settings()
        self.assertIsNone(self.client.get(self.base).json["iclicker"])

    def test_a_section_chooses_its_course_with_a_proposal_and_a_stale_check(self):
        route = f"/api/iclicker/sections/{self.sections[507]}"
        self.assertIsNone(self.client.get(route).json["linked"])
        self.assertEqual(self.fake.logins, 0)  # reading the link never contacts iClicker
        found = self.client.get(route + "/courses").json
        self.assertEqual(found["proposed"], self.course_a)
        self.assertEqual(found["courses"][0]["id"], self.course_a)  # the proposal first, archived last
        self.assertTrue(found["courses"][-1]["archived"])
        self.assertEqual(self.link(507, "not-a-course-of-yours").status_code, 400)
        linked = self.link(507, self.course_a)
        self.assertEqual(linked.status_code, 200, linked.json)
        self.assertEqual(linked.json["linked"]["name"], "Math-140-507-Fall-26")
        self.assertEqual(self.link(507, self.course_b).status_code, 409)  # the page showed no course: stale
        self.assertEqual(self.link(507, self.course_b, base=self.course_a).status_code, 200)
        self.assertEqual(self.client.get(self.base).json["iclicker"]["linked_sections"], [self.sections[507]])

    def test_the_assistants_link_is_stored_as_the_assistants(self):
        """requirement, 2026-11-06 (assistant.md: records that name an actor say the assistant made them, request_actor.py)."""
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        theirs = self.client.post(f"/api/iclicker/sections/{self.sections[507]}", json={"course_id": self.course_b, "base": self.course_a},
                                  headers=HEADERS, environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(theirs.status_code, 200, theirs.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT course_id, actor FROM iclicker_course_links ORDER BY rowid")],
                             [(self.course_a, "workspace"), (self.course_b, "assistant")])

    def test_a_refused_sign_in_is_a_502_with_its_reason(self):
        self.write_sign_in(EMAIL, "wrong")
        answer = self.client.get(f"/api/iclicker/sections/{self.sections[507]}/courses")
        self.assertEqual(answer.status_code, 502)
        self.assertIn("refused the saved sign-in", answer.json["error"])

    def test_a_refused_sign_in_is_not_tried_again_by_parallel_readers_or_later_reads(self):
        # 2026-10-01 audit: an expired token and a wrong saved password made one parallel read try the password four times
        # (docs/decisions/iclicker.md: a refused sign-in is never retried).
        courses = [self.course_a, self.course_b] + [self.fake.add_course(f"Math-140-5{n}-Fall-26") for n in (10, 11)]
        self.service.read_scores(courses)
        self.assertEqual(self.fake.logins, 1)
        self.fake.tokens.clear()  # iClicker no longer accepts the kept token
        self.write_sign_in(EMAIL, "a password changed elsewhere")

        def attempts():
            return [path for method, path in self.fake.requests if method == "POST"].count("/authproxy/login")

        before = attempts()
        for _ in range(2):  # the parallel read, then a later import
            with self.assertRaisesRegex(BadGateway, "refused the saved sign-in"):
                self.service.read_scores(courses)
        self.assertEqual(attempts() - before, 1)
        # Saved again (the right password): tried once more, and the parallel readers share that one new token.
        self.write_sign_in(EMAIL, PASSWORD)
        self.assertEqual(sorted(self.service.read_scores(courses)), sorted(courses))
        self.assertEqual(attempts() - before, 2)


class ImportTests(Seeded):
    """Import from iClicker (gradebook/README.md, "Importing from iClicker"; requirement, 2026-09-28: one column per session)."""

    def test_a_section_without_a_course_is_asked_to_choose_one(self):
        answer = self.client.post(self.base + "/iclicker/preview", json={"options": {}, "section_id": self.sections[507]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400)
        self.assertIn("Import from iClicker", answer.json["error"])

    def test_sessions_become_columns_of_the_section_and_are_remembered(self):
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        plan = self.preview(507)
        self.assertEqual([c["title"] for c in plan["columns"]], ["Session 1", "Session 2"])  # polls only, nothing still running
        self.assertEqual(plan["counts"]["not_polls"], 3)  # the exit poll, the quiz and the attendance check
        self.assertTrue(any("still running" in w for w in plan["warnings"]))
        self.assertTrue(any("matched by the email" in w for w in plan["warnings"]), plan["warnings"])
        self.assertEqual([s["name"] for s in plan["unmatched"]], ["Newcomer, Dana"])
        self.assertEqual({c["mapping"]["action"] for c in plan["columns"]}, {"new"})
        # 
        self.assertEqual({c["points"] for c in plan["columns"]}, {"10"})
        self.assertEqual(plan["new_categories"], ["Checks for Understanding Category"])
        self.assertEqual({(e["points"], e["category_name"]) for e in plan["new_items"]}, {("10", "Checks for Understanding Category")})
        self.assertEqual([e["sections"] for e in plan["new_items"]], [[self.sections[507]], [self.sections[507]]])
        changes = {(c["name"], c["column"].split(":")[1], c["action"]) for c in plan["changes"]}
        self.assertEqual(changes, {("Example, Alice", self.s1, "add"), ("Example, Bob", self.s1, "add"), ("Example, Alice", self.s2, "add")})
        text = json.dumps(plan)
        self.assertFalse(any(uin in text for uin in (ALICE, BOB, CAROL)))  # no UIN leaves the server
        self.assertNotIn("example.test", text)  # nor an email
        applied = self.apply(507, plan, "iclicker-1")
        self.assertEqual(applied.status_code, 200, applied.json)
        view = applied.json
        items = {item["title"]: item for item in view["setup"]["items"]}
        self.assertEqual((items["Session 1"]["points"], items["Session 1"]["sections"], items["Session 1"]["due_date"]), ("10", [self.sections[507]], ""))
        category = {c["id"]: c["name"] for c in view["setup"]["categories"]}
        self.assertEqual(category[items["Session 1"]["category_id"]], "Checks for Understanding Category")
        rows = {student["name"]: student["row"] for student in view["students"]}
        bob_s1 = view["cells"][items["Session 1"]["id"]][rows["Example, Bob"]]
        self.assertEqual((bob_s1["status"], bob_s1["points"], bob_s1["source"]), ("scored", "1", "import"))
        self.assertNotIn(rows["Example, Bob"], view["cells"].get(items["Session 2"]["id"], {}))  # never joined: nothing, unless ticked
        self.assertEqual(view["iclicker"]["linked_sections"], [self.sections[507]])
        self.assertIsNotNone(view["iclicker"]["last_import"])
        # Remembered by the session's ID: a renamed session still goes to its column, and the import is idempotent.
        self.fake.sessions[self.course_a][0]["name"] = "Renamed in iClicker"
        again = self.preview(507, {"missing_as_missing": True})
        first = next(c for c in again["columns"] if c["assignment_id"] == self.s1)
        self.assertEqual((first["mapping"], first["suggested"]), ({"action": "item", "item_id": items["Session 1"]["id"]}, "remembered"))
        self.assertEqual({(c["name"], c["action"], (c["proposed"] or {}).get("status")) for c in again["changes"] if c["action"] == "add"},
                         {("Example, Bob", "add", "missing")})
        replayed = self.apply(507, plan, "iclicker-1")
        self.assertEqual(replayed.status_code, 200)  # the same request replays
        # GRADEBOOK: the replay keeps the importing section's student and result scope.
        self.assertEqual(replayed.json["students"], view["students"])
        self.assertEqual(replayed.json["results"], view["results"])
        self.assertEqual({m for m, path in self.fake.requests if path != "/authproxy/login"}, {"GET"})

    def test_another_section_s_session_of_the_same_title_widens_the_column(self):
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.assertEqual(self.apply(507, self.preview(507), "iclicker-507").status_code, 200)
        self.assertEqual(self.link(508, self.course_b).status_code, 200)
        plan = self.preview(508)
        [column] = plan["columns"]
        view = self.client.get(self.base).json
        session_1 = next(item["id"] for item in view["setup"]["items"] if item["title"] == "Session 1")
        self.assertEqual((column["mapping"], column["suggested"]), ({"action": "item", "item_id": session_1}, "title"))
        self.assertEqual(plan["widen"], {session_1: [self.sections[508]]})
        self.assertEqual([(c["name"], c["action"]) for c in plan["changes"]], [("Example, Carol", "add")])
        applied = self.apply(508, plan, "iclicker-508")
        self.assertEqual(applied.status_code, 200, applied.json)
        item = next(item for item in applied.json["setup"]["items"] if item["id"] == session_1)
        self.assertEqual(item["sections"], sorted([self.sections[507], self.sections[508]]))
        rows = {student["name"]: student["row"] for student in applied.json["students"]}
        self.assertEqual(applied.json["cells"][session_1][rows["Example, Carol"]]["points"], "2")

    def test_a_typed_value_is_kept_unless_replace_is_ticked(self):
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.assertEqual(self.apply(507, self.preview(507), "iclicker-first").status_code, 200)
        view = self.client.get(self.base).json
        session_1 = next(item["id"] for item in view["setup"]["items"] if item["title"] == "Session 1")
        rows = {student["name"]: student["row"] for student in view["students"]}
        cell = view["cells"][session_1][rows["Example, Alice"]]
        typed = self.client.post(self.base + "/scores", json={"request_id": "typed", "changes": [
            {"item_id": session_1, "row": rows["Example, Alice"], "base": cell["id"], "status": "scored", "points": "1.5"}]}, headers=HEADERS)
        self.assertEqual(typed.status_code, 200, typed.json)
        plan = self.preview(507)
        alice = next(c for c in plan["changes"] if c["name"] == "Example, Alice" and c["item_id"] == session_1)
        self.assertEqual((alice["action"], alice["mismatch"]), ("kept", True))
        replaced = self.preview(507, {"replace": [rows["Example, Alice"]]})
        self.assertEqual(next(c for c in replaced["changes"] if c["name"] == "Example, Alice" and c["item_id"] == session_1)["action"], "update")

    def test_a_value_another_import_brought_in_is_kept_and_only_iclicker_s_own_values_are_updated(self):
        """ICLICKER KEEPS OTHERS (requirement, 2026-10-04):
        the 10 came in by another import (Canvas there, a file here), so iClicker keeps it unless Replace is ticked."""
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.assertEqual(self.apply(507, self.preview(507), "iclicker-first").status_code, 200)
        view = self.client.get(self.base).json
        session_1 = next(item["id"] for item in view["setup"]["items"] if item["title"] == "Session 1")
        rows = {student["name"]: student["row"] for student in view["students"]}
        content = f"UIN,Name,Session 1\r\n{ALICE},Alice,10\r\n".encode()
        options = json.dumps({"mapping": {"c2": {"action": "item", "item_id": session_1}}})
        preview = self.client.post(self.base + "/imports/preview", data={"file": (io.BytesIO(content), "scores.csv"), "options": options}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        filed = self.client.post(self.base + "/imports", data={"file": (io.BytesIO(content), "scores.csv"), "options": options,
                                                               "fingerprint": preview.json["fingerprint"], "request_id": "file-10"}, headers=HEADERS)
        self.assertEqual(filed.status_code, 200, filed.json)
        self.assertEqual(filed.json["cells"][session_1][rows["Example, Alice"]]["points"], "10")
        def rescore(student, participation, performance, by_enrollment=False):
            session = next(s for s in self.fake.sessions[self.course_a] if s["activityId"] == self.s1)
            key = "enrollmentId" if by_enrollment else "userId"
            session["userActivities"] = [u for u in session["userActivities"] if u[key] != student]
            self.fake.score(self.course_a, self.s1, student, participation, performance, by_enrollment)

        # iClicker changes Alice's (the file's 10) and Bob's (its own 1) in Session 1.
        rescore(self.alice, 0.0, 0.0)
        rescore(self.bob, 1.0, 1.0, by_enrollment=True)
        plan = self.preview(507)
        of = {c["name"]: c for c in plan["changes"] if c["item_id"] == session_1}
        self.assertEqual((of["Example, Alice"]["action"], of["Example, Alice"]["from"], of["Example, Alice"]["current"]["points"]), ("kept", "file", "10"))
        self.assertEqual((of["Example, Bob"]["action"], of["Example, Bob"].get("from")), ("update", None))
        applied = self.apply(507, plan, "iclicker-keeps")
        self.assertEqual(applied.status_code, 200, applied.json)
        cells = applied.json["cells"][session_1]
        self.assertEqual((cells[rows["Example, Alice"]]["points"], cells[rows["Example, Bob"]]["points"]), ("10", "2"))
        # Replace ticked for Alice: the file's value is overwritten, and the change still says where it came from.
        replaced = self.preview(507, {"replace": [rows["Example, Alice"]]})
        alice = next(c for c in replaced["changes"] if c["name"] == "Example, Alice" and c["item_id"] == session_1)
        self.assertEqual((alice["action"], alice["from"], alice["proposed"]["points"]), ("update", "file", "0"))
        applied = self.apply(507, replaced, "iclicker-replace")
        self.assertEqual(applied.status_code, 200, applied.json)
        self.assertEqual(applied.json["cells"][session_1][rows["Example, Alice"]]["points"], "0")
        # Now iClicker's own value: the next import updates it without a tick.
        rescore(self.alice, 1.0, 0.0)
        again = next(c for c in self.preview(507)["changes"] if c["name"] == "Example, Alice" and c["item_id"] == session_1)
        self.assertEqual((again["action"], again.get("from")), ("update", None))

    def test_an_existing_checks_for_understanding_category_is_used_and_scores_above_ten_are_named(self):
        saved = self.client.post(self.base + "/setup", json={"base_revision": None, "request_id": "cfu", "setup": {
            "categories": [{"ref": "c", "name": "Checks for Understanding"}]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        cfu = saved.json["setup"]["categories"][0]["id"]
        self.fake.score(self.course_a, self.s2, self.bob, 6.0, 6.0)
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        plan = self.preview(507)
        self.assertEqual(plan["new_categories"], [])
        self.assertEqual({(e["category_id"], e["category_name"]) for e in plan["new_items"]}, {(cfu, None)})
        self.assertIn("1 iClicker score is above a session's 10 points: recorded as iClicker has them.", plan["warnings"])

    def test_a_score_changed_by_hand_in_iclicker_counts(self):
        """HAND CHANGES COUNT (docs/decisions/iclicker.md; requirement, 2026-10-08): iClicker answers a hand change with adjusted points and no total, which
        Faculty read as no score, so the student's empty cell counted 0."""
        self.fake.adjust(self.course_a, self.s2, self.bob, 10.0)  # Bob did not answer Session 2; the instructor gave him 10
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        plan = self.preview(507, {"missing_as_missing": True})
        bob = next(c for c in plan["changes"] if c["name"] == "Example, Bob" and c["column"].endswith(self.s2))
        self.assertEqual((bob["action"], bob["proposed"]), ("add", {"points": "10", "status": "scored"}))  # never missing
        applied = self.apply(507, plan, "iclicker-hand")
        self.assertEqual(applied.status_code, 200, applied.json)
        session_2 = next(item["id"] for item in applied.json["setup"]["items"] if item["title"] == "Session 2")
        rows = {student["name"]: student["row"] for student in applied.json["students"]}
        self.assertEqual(applied.json["cells"][session_2][rows["Example, Bob"]]["points"], "10")
        # Each adjusted part stands in for its original; a record without adjusted parts reads as before.
        self.assertEqual(iclicker_import.points({"total": 2.0, "participation": 1.0, "performance": 1.0,
                                                 "adjusted_participation": None, "adjusted_performance": 0.0}), 1.0)
        self.assertEqual(iclicker_import.points({"total": None, "participation": 1.0, "performance": None}), 1.0)
        self.assertIsNone(iclicker_import.points({"total": None, "participation": None, "performance": None,
                                                  "adjusted_participation": None, "adjusted_performance": None}))

    def test_an_unset_iclicker_is_refused_plainly(self):
        self.service.settings = web.Settings()
        answer = self.client.post(self.base + "/iclicker/preview", json={"options": {}, "section_id": self.sections[507]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400)


class ExcusedAbsenceTests(Seeded):
    """An excused absence gives the iClicker sessions within its dates full credit (docs/decisions/absences.md ICLICKER-CREDIT;
    requirement, 2026-10-01)."""

    def rows(self):
        return {student["name"]: student["row"] for student in self.client.get(self.base).json["students"]}

    def items(self):
        return {item["title"]: item["id"] for item in self.client.get(self.base).json["setup"]["items"]}

    def cell(self, title, name):
        view = self.client.get(self.base).json
        item = next(item["id"] for item in view["setup"]["items"] if item["title"] == title)
        rows = {student["name"]: student["row"] for student in view["students"]}
        return view["cells"].get(item, {}).get(rows[name])

    def record(self, name, first, last, status="excused", request_id=None):
        section_id, enrollment_id = self.rows()[name].split(":")
        answer = self.client.post("/api/absences", json={"section_id": section_id, "enrollment_id": enrollment_id, "first_date": first,
                                                         "last_date": last, "reason": "illness", "status": status,
                                                         "request_id": request_id or f"absence-{first}-{last}-{status}-{name.split()[-1]}"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 201, answer.json)
        return answer.json

    def revise(self, absence, request_id, **facts):
        answer = self.client.post(f"/api/absences/{absence['id']}", json={"base_revision": absence["revision"], "request_id": request_id, **facts},
                                  headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def imported(self, number, request_id, options=None):
        answer = self.apply(number, self.preview(number, options), request_id)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def test_a_value_put_back_keeps_its_origin_so_iclicker_still_updates_it(self):
        """KEEPS OTHERS: a put-back is the iClicker value restored, not another source's, so the next import updates it."""
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.imported(507, "import-1")
        absence = self.record("Example, Bob", "2026-09-01", "2026-09-01")
        self.assertEqual(self.cell("Session 1", "Example, Bob")["points"], "10")
        self.assertEqual(self.revise(absence, "undo", status="not_excused")["iclicker_credit"], {"filled": 0, "put_back": 1})
        self.assertEqual(self.cell("Session 1", "Example, Bob")["points"], "1")
        session = next(s for s in self.fake.sessions[self.course_a] if s["activityId"] == self.s1)
        session["userActivities"] = [u for u in session["userActivities"] if u["enrollmentId"] != self.bob]
        self.fake.score(self.course_a, self.s1, self.bob, 1.0, 1.0, by_enrollment=True)
        bob = next(c for c in self.preview(507)["changes"] if c["name"] == "Example, Bob" and c["item_id"] == self.items()["Session 1"])
        self.assertEqual((bob["action"], bob.get("from"), bob["proposed"]["points"]), ("update", None, "2"))

    def test_excusing_after_the_import_fills_the_sessions_within_its_dates_and_a_new_decision_puts_them_back(self):
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.imported(507, "import-1")
        self.assertEqual(self.cell("Session 1", "Example, Bob")["points"], "1")
        self.assertIsNone(self.cell("Session 2", "Example, Bob"))  # never joined

        absence = self.record("Example, Bob", "2026-09-03", "2026-09-03")
        self.assertEqual(absence["iclicker_credit"], {"filled": 1, "put_back": 0})
        filled = self.cell("Session 2", "Example, Bob")
        self.assertEqual((filled["status"], filled["points"], filled["source"], filled["note"]),
                         ("scored", "10", "instructor", "Excused absence (Thu, Sep 3): full credit."))
        self.assertEqual(self.cell("Session 1", "Example, Bob")["points"], "1")  # Sep 1: outside the absence
        self.assertEqual(self.cell("Session 2", "Example, Alice")["points"], "2")  # another student's
        self.assertEqual([(c["title"], c["date"], c["points"]) for c in self.client.get(f"/api/absences/{absence['id']}").json["iclicker"]],
                         [("Session 2", "2026-09-03", "10")])
        changes = self.client.get(self.base + "/changes").json["changes"]
        self.assertEqual((changes[0]["origin"], changes[0]["summary"]["action"], changes[0]["cells"]["added"]), ("absence", "fill", 1))
        history = self.client.get(self.base + "/history", query_string={"item": self.items()["Session 2"], "row": self.rows()["Example, Bob"]}).json
        self.assertEqual(history["revisions"][0]["request"]["summary"]["absence_id"], absence["id"])
        # A later import keeps the credit (entered for the instructor's excuse) and fills nothing twice.
        again = self.preview(507, {"missing_as_missing": True})
        bob = next(c for c in again["changes"] if c["name"] == "Example, Bob" and c["item_id"] == self.items()["Session 2"])
        self.assertEqual(bob["action"], "kept")
        self.imported(507, "import-2", {"missing_as_missing": True})
        self.assertEqual(self.cell("Session 2", "Example, Bob")["points"], "10")

        # Not excused after all: the cell is empty again, as the import left it, and the next import may fill it.
        undone = self.revise(absence, "undo", status="not_excused")
        self.assertEqual(undone["iclicker_credit"], {"filled": 0, "put_back": 1})
        self.assertEqual(undone["iclicker"], [])
        self.assertIsNone(self.cell("Session 2", "Example, Bob"))
        later = self.preview(507, {"missing_as_missing": True})
        bob = next(c for c in later["changes"] if c["name"] == "Example, Bob" and c["item_id"] == self.items()["Session 2"])
        self.assertEqual((bob["action"], bob["proposed"]["status"]), ("add", "missing"))
        self.assertEqual(self.client.get(self.base + "/changes").json["changes"][0]["summary"]["action"], "put_back")

        # Excused again: full credit again. Changed by hand afterwards: the change stands through later saves and imports.
        again = self.revise(undone, "redo", status="excused")
        self.assertEqual(again["iclicker_credit"], {"filled": 1, "put_back": 0})
        cell = self.cell("Session 2", "Example, Bob")
        typed = self.client.post(self.base + "/scores", json={"request_id": "typed", "changes": [
            {"item_id": self.items()["Session 2"], "row": self.rows()["Example, Bob"], "base": cell["id"], "status": "scored", "points": "7"}]},
            headers=HEADERS)
        self.assertEqual(typed.status_code, 200, typed.json)
        noted = self.revise(again, "note", decision_note="Doctor's note")
        self.assertNotIn("iclicker_credit", noted)
        self.imported(507, "import-3")
        self.assertEqual(self.cell("Session 2", "Example, Bob")["points"], "7")
        self.assertEqual(noted["iclicker"], [])

    def test_moving_the_dates_puts_back_what_they_no_longer_cover(self):
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.imported(507, "import-1")
        absence = self.record("Example, Alice", "2026-09-01", "2026-09-03")
        self.assertEqual(absence["iclicker_credit"], {"filled": 2, "put_back": 0})
        moved = self.revise(absence, "moved", first_date="2026-09-03")
        self.assertEqual(moved["iclicker_credit"], {"filled": 0, "put_back": 1})
        back = self.cell("Session 1", "Example, Alice")
        self.assertEqual((back["status"], back["points"], back["source"], back["note"]), ("scored", "2", "import", ""))  # exactly as imported
        self.assertEqual(self.cell("Session 2", "Example, Alice")["points"], "10")

    def test_excusing_before_the_import_fills_at_the_import_for_the_sessions_section_only(self):
        early = self.record("Example, Alice", "2026-09-01", "2026-09-02")
        self.assertNotIn("iclicker_credit", early)  # no iClicker import yet: nothing to fill
        self.assertEqual(early["iclicker"], [])
        self.record("Example, Carol", "2026-09-02", "2026-09-02")
        self.record("Example, Bob", "2026-09-01", "2026-09-01", status="pending")  # waiting: no credit
        self.fake.score(self.course_a, self.s2, self.bob, 5.0, 5.0)
        self.record("Example, Bob", "2026-09-03", "2026-09-03", request_id="bob-full")  # already full credit: nothing written
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        view = self.imported(507, "import-507")
        filled = self.cell("Session 1", "Example, Alice")
        self.assertEqual((filled["points"], filled["source"]), ("10", "instructor"))
        self.assertEqual(self.cell("Session 2", "Example, Alice")["points"], "2")  # Sep 3: outside her absence
        self.assertEqual((self.cell("Session 1", "Example, Bob")["points"], self.cell("Session 1", "Example, Bob")["source"]), ("1", "import"))
        bob_s2 = self.cell("Session 2", "Example, Bob")
        self.assertEqual((bob_s2["points"], bob_s2["source"]), ("10", "import"))
        absences = [c for c in view["requests"] if c["summary"].get("source") == "absence"]
        self.assertEqual([(c["kind"], c["summary"]["action"], c["summary"]["cells"]) for c in absences], [("scores", "fill", 1)])
        # Carol's section has its own iClicker course: its Sep 2 session goes to the same column, and only its import fills it.
        self.assertEqual(self.link(508, self.course_b).status_code, 200)
        self.imported(508, "import-508")
        carol = self.cell("Session 1", "Example, Carol")
        self.assertEqual((carol["points"], carol["note"]), ("10", "Excused absence (Wed, Sep 2): full credit."))
        with connect(self.app.config["DATABASE"]) as db:
            dates = {(row["course_id"], row["session_id"]): row["session_date"] for row in db.execute("SELECT * FROM gradebook_iclicker_sessions")}
        self.assertEqual(dates, {(self.course_a, self.s1): "2026-09-01", (self.course_a, self.s2): "2026-09-03", (self.course_b, self.b1): "2026-09-02"})

    def test_the_preview_names_what_the_import_gives_full_credit_and_import_writes_exactly_that(self):
        """(the import's "When you press Import" names the sessions)."""
        self.record("Example, Bob", "2026-09-01", "2026-09-10")      # scored 1 on Session 1, never joined Session 2
        alice = self.record("Example, Alice", "2026-09-03", "2026-09-03", status="pending")
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        early = self.preview(507)
        self.assertEqual(early["excused"], {"cells": 2, "points": "10", "kept": 0, "students": [
            {"name": "Example, Bob", "section": 507, "sessions": [{"title": "Session 1", "date": "2026-09-01", "day": "Tue, Sep 1"},
                                                                  {"title": "Session 2", "date": "2026-09-03", "day": "Thu, Sep 3"}]}]})
        # Alice's absence is decided between the preview and Import: Import asks for a new preview, since its line changed.
        self.revise(alice, "decided", status="excused")
        stale = self.apply(507, early, "stale")
        self.assertEqual(stale.status_code, 409, stale.json)
        plan = self.preview(507)
        self.assertEqual(plan["excused"]["cells"], 3)
        self.assertEqual([(s["name"], [x["title"] for x in s["sessions"]]) for s in plan["excused"]["students"]],
                         [("Example, Alice", ["Session 2"]), ("Example, Bob", ["Session 1", "Session 2"])])
        self.assertNotIn("001234567", json.dumps(plan["excused"]))
        self.assertEqual(self.apply(507, plan, "import-1").status_code, 200)
        for title, name in (("Session 1", "Example, Bob"), ("Session 2", "Example, Bob"), ("Session 2", "Example, Alice")):
            self.assertEqual(self.cell(title, name)["points"], "10", (title, name))
        self.assertEqual(self.cell("Session 1", "Example, Alice")["points"], "2")
        # The next preview gives nothing again: the credited values iClicker disagrees with are kept, and said to be credit.
        again = self.preview(507, {"missing_as_missing": True})
        self.assertEqual((again["excused"]["cells"], again["excused"]["kept"], again["counts"]["kept"]), (0, 3, 3))
        # No absence of these students: the plan carries no line.
        self.assertNotIn("excused", self.preview(508) if self.link(508, self.course_b).status_code == 200 else {})

    def test_two_absences_covering_one_session_keep_its_credit_until_neither_does(self):
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.imported(507, "import-1")
        first = self.record("Example, Bob", "2026-09-02", "2026-09-03")
        second = self.record("Example, Bob", "2026-09-03", "2026-09-04")
        self.assertEqual((first["iclicker_credit"], "iclicker_credit" in second), ({"filled": 1, "put_back": 0}, False))
        withdrawn = self.revise(first, "w1", status="withdrawn")
        self.assertNotIn("iclicker_credit", withdrawn)  # the second absence still covers Sep 3
        self.assertEqual(self.cell("Session 2", "Example, Bob")["points"], "10")
        self.assertEqual(self.revise(second, "w2", status="withdrawn")["iclicker_credit"], {"filled": 0, "put_back": 1})
        self.assertIsNone(self.cell("Session 2", "Example, Bob"))

    def test_only_iclicker_columns_and_never_an_archived_one(self):
        saved = self.client.post(self.base + "/setup", json={"base_revision": None, "request_id": "quiz", "setup": {
            "items": [{"title": "Quiz 1", "points": "10", "due_date": "2026-09-03"}]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        view = self.imported(507, "import-1")
        setup = {key: value for key, value in view["setup"].items() if key not in ("revision", "created_at", "actor")}
        archived = {**setup, "items": [{**item, "archived": True} if item["title"] == "Session 2" else item for item in setup["items"]]}
        answer = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": "archive", "setup": archived},
                                  headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        absence = self.record("Example, Alice", "2026-09-01", "2026-09-03")
        self.assertEqual(absence["iclicker_credit"], {"filled": 1, "put_back": 0})  # Session 1 only
        self.assertIsNone(self.cell("Quiz 1", "Example, Alice"))


class AfterClassTests(Seeded):
    """ICLICKER AFTER CLASS (docs/decisions/iclicker.md; requirement, 2026-10-06): each section's import runs by itself once its class ends, on the
    Term schedule's class days, recording who did not answer today's sessions as missing."""

    MONDAY = "2026-10-05"
    BEFORE = datetime(2026, 10, 5, 15, 5, tzinfo=timezone.utc)   # 10:05 am Central: 507's class ended at 10:00
    AFTER = datetime(2026, 10, 5, 15, 11, tzinfo=timezone.utc)   # 10:11 am: ten minutes after it

    def setUp(self):
        super().setUp()
        self.after = self.app.extensions["iclicker_after_class"]
        self.assertEqual(self.link(507, self.course_a).status_code, 200)
        self.today = self.fake.add_session(self.course_a, "Class 20", "2026-10-05T14:20:00.000Z")  # 9:20 am Central
        self.fake.score(self.course_a, self.today, self.alice, 1.0, 1.0)
        self.schedule()

    def schedule(self, used=True):
        """MATH 140's Term schedule: 507 MWF 9:10-10:00, 508 MWF 1:50-2:40, Labor Day off; in use."""
        with connect(self.app.config["DATABASE"]) as db:
            entries = []
            for number, start, end in ((507, "09:10", "10:00"), (508, "13:50", "14:40")):
                entry = f"class-{number}-entry"
                db.execute("INSERT INTO scheduling_entries(id,term_id,section_id,kind,created_at) VALUES(?,?,?,?,?)",
                           (entry, self.term, self.sections[number], "class", "2026-08-01T00:00:00+00:00"))
                entries.append({"entry": entry, "weekdays": [0, 2, 4], "start": start, "end": end, "room": "HELD 107", "seats_makeup": False})
            db.execute("""INSERT INTO scheduling_revisions(term_id,revision,first_day,last_day,entries_json,request_id,request_sha256,actor,created_at,days_off_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (self.term, 1, "2026-08-24", "2026-12-03", json.dumps(entries), "schedule-1", "x" * 64, "workspace",
                                                 "2026-08-01T00:00:00+00:00", json.dumps(["2026-09-07"])))
            if used:
                db.execute("INSERT INTO scheduling_uses VALUES(?,?,?,?,?,?,?,?,?,?)", ("use-00000001", self.term, "use", 1, "f", "use-1", "x", "{}",
                                                                                     "workspace", "2026-08-01T00:00:00+00:00"))

    def runs(self, number=507):
        with connect(self.app.config["DATABASE"]) as db:
            return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in db.execute(
                "SELECT class_date,status,detail_json,request_ref FROM gradebook_iclicker_runs WHERE section_id=? ORDER BY created_at,rowid",
                (self.sections[number],))]

    def cell(self, title, name):
        view = self.client.get(self.base).json
        item = next(item["id"] for item in view["setup"]["items"] if item["title"] == title)
        rows = {student["name"]: student["row"] for student in view["students"]}
        return view["cells"].get(item, {}).get(rows[name])

    def test_once_the_class_ends_today_s_session_comes_in_and_who_did_not_answer_is_missing(self):
        self.assertEqual(self.after.round(self.BEFORE), {})
        self.assertEqual(self.after.round(self.AFTER), {self.sections[507]: "imported"})
        self.assertEqual(self.cell("Class 20", "Example, Alice")["points"], "2")
        self.assertEqual(self.cell("Class 20", "Example, Bob")["status"], "missing")  # today's session, not answered: 0
        self.assertIsNone(self.cell("Session 2", "Example, Bob"))  # an earlier class's session: never made 0 by this run
        [run] = self.runs()
        self.assertEqual((run["class_date"], run["status"], run["detail"]["sessions"], run["detail"]["running"]),
                         (self.MONDAY, "imported", [{"id": self.today, "title": "Class 20"}], 0))
        with connect(self.app.config["DATABASE"]) as db:
            summary = json.loads(db.execute("SELECT summary_json FROM gradebook_requests WHERE id=?", (run["request_ref"],)).fetchone()[0])
        self.assertEqual((summary["source"], summary["automatic"], summary["section_id"]), ("iclicker", "iclicker", self.sections[507]))
        self.assertEqual(self.after.round(self.AFTER + timedelta(hours=1)), {})  # the day is done
        self.assertEqual(self.client.get(self.base).json["iclicker"]["after_class"][self.sections[507]]["status"], "imported")
        # 508 links no iClicker course: its run (after 2:50 pm) records that once.
        self.assertEqual(self.after.round(datetime(2026, 10, 5, 19, 51, tzinfo=timezone.utc)), {self.sections[508]: "not_linked"})
        self.assertEqual(self.after.round(datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)), {})

    def test_an_excused_absence_today_is_credited_when_the_class_ends(self):
        section_id, enrollment_id = {s["name"]: s["row"] for s in self.client.get(self.base).json["students"]}["Example, Bob"].split(":")
        answer = self.client.post("/api/absences", json={"section_id": section_id, "enrollment_id": enrollment_id, "first_date": self.MONDAY,
                                                         "last_date": self.MONDAY, "reason": "illness", "status": "excused",
                                                         "request_id": "bob-monday"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 201, answer.json)
        self.after.round(self.AFTER)
        bob = self.cell("Class 20", "Example, Bob")
        self.assertEqual((bob["status"], bob["points"]), ("scored", "10"))

    def credit(self, name, day, request_id, reason="iClicker would not open in class", number=507):
        section_id, enrollment_id = {s["name"]: s["row"] for s in self.client.get(self.base).json["students"]}[name].split(":")
        return self.client.post(self.base + "/iclicker/credits", json={"section_id": section_id, "enrollment_id": enrollment_id, "class_date": day,
                                                                       "reason": reason, "request_id": request_id}, headers=HEADERS)

    def test_a_credit_given_during_class_is_filled_when_the_class_ends(self):
        # CREDIT BEFORE THE IMPORT (requirement, 2026-10-21): a student who could not open iClicker emailed before class ended.
        given = self.credit("Example, Bob", self.MONDAY, "bob-credit")
        self.assertEqual(given.status_code, 200, given.json)
        self.assertEqual((given.json["credit"]["state"], given.json["given"], given.json["filled"]), ("waiting", [], 0))
        self.assertEqual(self.credit("Example, Bob", self.MONDAY, "bob-credit").json["credit"], given.json["credit"])  # a replay
        self.assertEqual(self.credit("Example, Bob", self.MONDAY, "bob-credit", reason="other").status_code, 409)
        self.assertEqual(self.credit("Example, Bob", self.MONDAY, "bob-again").json["credit"]["credit_id"], given.json["credit"]["credit_id"])
        self.after.round(self.AFTER)
        bob = self.cell("Class 20", "Example, Bob")
        self.assertEqual((bob["status"], bob["points"], bob["source"], bob["note"]),
                         ("scored", "10", "instructor", "iClicker would not open in class: full credit."))
        self.assertEqual(self.cell("Class 20", "Example, Alice")["points"], "2")  # only the student credited
        self.assertIsNone(self.cell("Session 2", "Example, Bob"))                # only that day
        changes = self.client.get(self.base + "/changes").json["changes"]
        self.assertEqual((changes[0]["origin"], changes[0]["summary"]["action"]), ("iclicker_credit", "fill"))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_iclicker_credits").fetchone()[0], 1)
        # Imported again (Import from iClicker): the credit is kept, and nothing is filled twice.
        plan = self.preview(507, {"missing_as_missing": True})
        self.assertEqual(self.apply(507, plan, "again").status_code, 200)
        self.assertEqual(self.cell("Class 20", "Example, Bob")["points"], "10")
        # Taken back: the import's 0 again.
        withdrawn = self.client.post(f"{self.base}/iclicker/credits/{given.json['credit']['credit_id']}/withdraw", json={"request_id": "take-back"},
                                     headers=HEADERS)
        self.assertEqual(withdrawn.status_code, 200, withdrawn.json)
        self.assertEqual((withdrawn.json["credit"]["state"], withdrawn.json["put_back"], withdrawn.json["given"]), ("withdrawn", 1, []))
        self.assertEqual(self.cell("Class 20", "Example, Bob")["status"], "missing")

    def test_a_credit_after_the_import_fills_at_once_and_is_refused_where_it_cannot_apply(self):
        self.after.round(self.AFTER)
        given = self.credit("Example, Bob", self.MONDAY, "late-credit", reason="Photo of ID and the question, 9:40 am")
        self.assertEqual(given.status_code, 200, given.json)
        self.assertEqual((given.json["filled"], [g["title"] for g in given.json["given"]]), (1, ["Class 20"]))
        self.assertEqual(self.cell("Class 20", "Example, Bob")["points"], "10")
        self.assertEqual(self.credit("Example, Bob", "2999-01-01", "future").status_code, 400)
        self.assertEqual(self.credit("Example, Bob", "10/05/2026", "bad-date").status_code, 400)
        self.assertEqual(self.credit("Example, Bob", self.MONDAY, "no-reason", reason=" ").status_code, 400)
        refused = self.credit("Example, Carol", self.MONDAY, "carol")  # section 508 links no iClicker course
        self.assertEqual(refused.status_code, 400)
        self.assertIn("links no iClicker course", refused.json["error"])

    def test_follow_ups_are_told_how_to_give_credit_and_what_was_given(self):
        from assistant.followups import FollowupSuggestions
        self.assertEqual(self.credit("Example, Bob", self.MONDAY, "bob-credit").status_code, 200)
        bob = {s["name"]: s["row"] for s in self.client.get(self.base).json["students"]}["Example, Bob"]
        with connect(self.app.config["DATABASE"]) as db, patch("scheduling.clock.campus_today", return_value=date(2026, 10, 5)):
            term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (self.term,)).fetchone())
            lines = FollowupSuggestions._iclicker_facts(db, {"enrollment_id": bob.split(":")[1]}, term, [self.sections[507]], "2026-10-05", "2026-10-05")
        route = next(line for line in lines if line.startswith("iClicker credit for a class day"))
        self.assertIn(f"POST /api/gradebook/terms/{self.term}/iclicker/credits", route)
        self.assertIn(f'"enrollment_id": "{bob.split(":")[1]}"', route)
        self.assertIn(f"section 507: {self.sections[507]}", route)
        self.assertIn("at most 200 characters", route)  # a 220-character reason was refused at Approve (2026-10-21)
        self.assertTrue(any(line.startswith("- Mon, Oct 5 · section 507: given (“iClicker would not open in class”; credit ") for line in lines))

    def test_a_session_still_running_is_looked_at_again_and_an_empty_one_makes_no_column(self):
        running = self.fake.add_session(self.course_a, "Class 20b", "2026-10-05T14:50:00.000Z", ended=False)
        self.fake.score(self.course_a, running, self.alice, 1.0, 0.0)
        self.fake.add_session(self.course_a, "Nobody", "2026-10-05T14:55:00.000Z")  # ended, nobody answered
        self.after.round(self.AFTER)
        self.assertEqual(self.runs()[-1]["detail"]["running"], 1)
        titles = [item["title"] for item in self.client.get(self.base).json["setup"]["items"]]
        self.assertNotIn("Nobody", titles)
        self.assertNotIn("Class 20b", titles)
        self.assertEqual(self.after.round(self.AFTER + timedelta(minutes=2)), {})  # not again so soon
        next(s for s in self.fake.sessions[self.course_a] if s["activityId"] == running)["ended"] = "2026-10-05T15:05:00.000Z"
        with patch.object(iclicker_after_class, "RUNNING", -10 ** 9):
            self.assertEqual(self.after.round(self.AFTER + timedelta(minutes=20)), {self.sections[507]: "imported"})
        self.assertIn("Class 20b", [item["title"] for item in self.client.get(self.base).json["setup"]["items"]])
        self.assertEqual(self.after.round(self.AFTER + timedelta(minutes=40)), {})

    def test_follow_ups_know_which_class_days_had_an_iclicker_and_who_answered(self):
        from assistant.followups import FollowupSuggestions
        self.after.round(self.AFTER)
        self.after.round(datetime(2026, 10, 7, 15, 11, tzinfo=timezone.utc))  # Wednesday: no session that day
        bob = {s["name"]: s["row"] for s in self.client.get(self.base).json["students"]}["Example, Bob"]
        with connect(self.app.config["DATABASE"]) as db, patch("scheduling.clock.campus_today", return_value=date(2026, 10, 9)):
            term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (self.term,)).fetchone())
            lines = FollowupSuggestions._iclicker_facts(db, {"enrollment_id": bob.split(":")[1]}, term, [self.sections[507]], "2026-10-05", "2026-10-09")
        self.assertIn("a missed one is excused only with documentation", lines[0])  # DOCUMENTATION ONLY FOR MISSED WORK (2026-10-09)
        self.assertIn("- Mon, Oct 5 · section 507: “Class 20” given; they did not answer (0)", lines)
        self.assertIn("- Wed, Oct 7 · section 507: no iClicker given", lines)
        self.assertIn("- Fri, Oct 9 · section 507: not checked yet (iClicker is imported after each class)", lines)

    def test_a_day_not_checked_yet_reads_the_other_sections_that_day(self):
        # ONE ICLICKER FOR EVERY SECTION (requirement, 2026-10-09): before 508's
        # own import, 507's tells what to expect there that day: a session given, or a finished import with none.
        from assistant.followups import FollowupSuggestions
        self.assertEqual(self.link(508, self.course_b).status_code, 200)
        self.assertEqual(self.after.round(self.AFTER), {self.sections[507]: "imported"})  # Monday: "Class 20"; 508 meets at 1:50
        self.after.round(datetime(2026, 10, 7, 15, 11, tzinfo=timezone.utc))  # Wednesday: 507 had none; 508 not read yet
        carol = {s["name"]: s["row"] for s in self.client.get(self.base).json["students"]}["Example, Carol"]
        with connect(self.app.config["DATABASE"]) as db, patch("scheduling.clock.campus_today", return_value=date(2026, 10, 9)):
            term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (self.term,)).fetchone())
            lines = FollowupSuggestions._iclicker_facts(db, {"enrollment_id": carol.split(":")[1]}, term, [self.sections[508]], "2026-10-05", "2026-10-09")
        self.assertIn("- Mon, Oct 5 · section 508: not imported for this section yet; an iClicker was given in section 507 that day, and "
                      "the instructor gives one in every section or none, so one is expected here too", lines)
        self.assertIn("- Wed, Oct 7 · section 508: not imported for this section yet; section 507 had no iClicker that day, and the "
                      "instructor gives one in every section or none, so none is expected", lines)
        self.assertIn("- Fri, Oct 9 · section 508: not checked yet (iClicker is imported after each class)", lines)  # nobody has read Friday

    def test_no_class_no_schedule_no_run(self):
        self.assertEqual(self.after.round(datetime(2026, 10, 6, 15, 11, tzinfo=timezone.utc)), {})  # Tuesday: no class
        self.assertEqual(self.after.round(datetime(2026, 9, 7, 15, 11, tzinfo=timezone.utc)), {})   # Labor Day: a day off
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO scheduling_uses VALUES(?,?,?,?,?,?,?,?,?,?)", ("undo-0000001", self.term, "undo", 1, "f", "undo-1", "x", "{}",
                                                                                 "workspace", "2026-08-02T00:00:00+00:00"))
        self.assertEqual(self.after.round(self.AFTER), {})  # the term no longer uses its schedule
        self.assertFalse(self.client.get(self.base).json["iclicker"]["scheduled"])

    def test_iclicker_failing_is_recorded_and_tried_again_later(self):
        self.fake.fail = True
        self.assertEqual(self.after.round(self.AFTER), {self.sections[507]: "failed"})
        self.assertTrue(self.runs()[-1]["detail"]["problem"])
        self.assertEqual(self.after.round(self.AFTER + timedelta(minutes=5)), {})
        self.fake.fail = False
        with patch.object(iclicker_after_class, "RETRY", -10 ** 9):
            self.assertEqual(self.after.round(self.AFTER + timedelta(hours=1)), {self.sections[507]: "imported"})


if __name__ == "__main__":
    unittest.main()
