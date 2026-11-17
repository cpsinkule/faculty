"""Import from Howdy (CANVAS "IMPORT FROM HOWDY"): Howdy's roster rows become the CSV Howdy's own export makes, read by the
ordinary roster import; course rows, term codes and pages are recognized; the start route plans the course page's sections and
keeps the workspace guard. The browser itself is driven end to end in tests/browser_howdy_import.py. Everything is synthetic."""
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import howdy_import
from app import create_app
from roster_import import parse_roster
from rosters import leaving_reason
from test_storage import HEADERS

ROWS = [
    {"FULL_NAME": "Aggie, Reveille Ann", "UIN": "012345678", "STVRSTS_DESC": "Web Registered", "MAJOR": "Economics",
     "CLASSIFICATION": "U1", "DEGREE_CANDIDATE": None, "MIDTERM_REQUIRED": "N", "SFRSTCR_GRDE_CODE_MID": None,
     "SFRSTCR_GRDE_CODE": None, "UPDATED_FINAL_GRADE": None, "EMAIL_ADDRESS": "rev@email.tamu.edu", "SFRSTCR_PIDM": 99},
    {"FULL_NAME": "Doe, Jordan", "UIN": 123456789.0, "STVRSTS_DESC": "Q-Drop State Limited", "MAJOR": "Finance",
     "CLASSIFICATION": "U2", "SFRSTCR_GRDE_CODE": "Q", "EMAIL_ADDRESS": "jd@email.tamu.edu"},
]


class FakeBrowser:
    """Stands in for HowdyBrowser in the route test: records what it was started with."""
    started = []

    def __init__(self, portal, netid=None, password=None, plan=None):
        self.id, self.state = "session-1", "starting"
        FakeBrowser.started.append({"portal": portal, "netid": netid, "plan": plan})

    def status(self):
        return {"id": self.id, "state": self.state, "files": []}

    def watch(self):
        pass

    def close(self):
        self.state = "closed"

    def expired(self):
        return False


class RosterFileTests(unittest.TestCase):
    def test_howdy_rows_become_the_export_the_roster_import_reads(self):
        content = howdy_import.roster_csv(ROWS)
        self.assertTrue(content.startswith(b'"Name","UIN","Reg\nStatus","Major"'))
        self.assertIn(b"\r\n", content)
        parsed = parse_roster(content, "MATH-140-507_202631-11-01-2026.csv")
        self.assertEqual(parsed["headers"], ["Name", "UIN", "Reg Status", "Major", "Class", "Degree Candidates", "Midterm Required",
                                             "Midterm", "Final", "Updated Final", "Email"])
        first, second = parsed["students"]
        self.assertEqual(first["uin"], "012345678")
        self.assertEqual(first["fields"]["Name"], "Aggie, Reveille Ann")
        self.assertEqual(first["fields"]["Degree Candidates"], "")
        self.assertEqual(second["uin"], "123456789", "a UIN Howdy sends as a number keeps nine digits")
        # The registrar's Q drop reads as it does on an uploaded export: the import drops the student.
        self.assertEqual(leaving_reason(second["fields"]), "Q drop")
        self.assertEqual(leaving_reason(first["fields"]), "")
        self.assertNotIn(b"99", content.split(b"\r\n", 1)[1].split(b",")[0], "fields Howdy's export does not show stay out")

    def test_a_short_numeric_uin_regains_its_leading_zeros(self):
        parsed = parse_roster(howdy_import.roster_csv([{"FULL_NAME": "A, B", "UIN": 12345678}]), "r.csv")
        self.assertEqual(parsed["students"][0]["uin"], "012345678")


class RecognitionTests(unittest.TestCase):
    def test_course_rows_term_codes_and_wrapped_lists(self):
        self.assertEqual(howdy_import.section_of({"COURSE": "MATH-140-507"}), ("MATH-140", 507))
        self.assertEqual(howdy_import.section_of({"COURSE": "MATH 140 001"}), ("MATH-140", 1))
        self.assertIsNone(howdy_import.section_of({"COURSE": "Office hours"}))
        self.assertEqual(howdy_import.course_code("math 140"), "MATH-140")
        self.assertEqual(howdy_import.term_codes("Fall", 2026), ["202631"])
        self.assertEqual(howdy_import.term_codes("Spring", "2027"), ["202711"])
        self.assertEqual(howdy_import.term_codes("Summer", 2027), ["202721"])
        self.assertEqual(howdy_import.term_codes("Other", 2027), [])
        wrapped = {"data": {"rows": [{"CRN": "33168", "COURSE": "MATH-140-507"}, {"CRN": "1", "COURSE": "MATH-142-501"}]}}
        self.assertEqual([row["CRN"] for row in howdy_import.records(wrapped, "CRN")], ["33168", "1"])
        terms = [{"termCode": "202631", "description": "Fall 2026 - College Station"},
                 {"termCode": "202632", "description": "Fall 2026 - Galveston"}, {"termCode": "202711"}]
        self.assertEqual(howdy_import.other_codes(terms, "20263"), ["202631", "202632"])

    def test_each_howdy_page_is_recognized(self):
        browser = howdy_import.HowdyBrowser.__new__(howdy_import.HowdyBrowser)
        browser.host = "howdyportal.tamu.edu"
        step = lambda url, text, inputs=(): browser._classify(url, text, list(inputs))  # noqa: E731
        self.assertEqual(step("https://howdyportal.tamu.edu/uPortal/azureSignIn", "Performing security verification"), "checking")
        self.assertEqual(step("https://howdyportal.tamu.edu/uPortal/azureSignIn", ""), "working")
        self.assertEqual(step("https://howdyportal.tamu.edu/uPortal/normal/render.uP", "It looks like the page does not exist. Sign In"), "guest")
        self.assertEqual(step("https://howdy.tamu.edu/main/home/card-view", "Bus Routes Canvas"), "guest")
        self.assertEqual(step("https://howdyportal.tamu.edu/uPortal/f/howdy-dashboard/normal/render.uP", "My Courses Sign Out"), "howdy")
        self.assertEqual(step("https://howdyportal.tamu.edu/uPortal/f/howdy-dashboard/normal/render.uP", "Loading"), "working")
        self.assertEqual(step("https://login.microsoftonline.com/x/oauth2/v2.0/authorize", "Sign in", [{"type": "email", "name": "loginfmt"}]), "netid")
        self.assertEqual(step("https://api-1.duosecurity.com/frame/v4/auth/prompt", "Check for a Duo Push"), "duo_push")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()
        self.client.get("/").close()
        patcher = mock.patch.object(howdy_import, "HowdyBrowser", FakeBrowser)
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeBrowser.started.clear()

    def tearDown(self):
        self.directory.cleanup()

    def course(self, **values):
        found = self.client.post("/api/courses", json={"name": "Math for Business", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                       "sections": [507, 508], "multiple_sections": True, "is_current": True, **values},
                                 headers=HEADERS)
        self.assertEqual(found.status_code, 201, found.json)
        return next(group for group in self.client.get("/api/state").json["course_groups"] if group["code"] == values.get("code", "MATH 140"))

    def test_the_start_plans_the_course_pages_sections_and_keeps_the_guard(self):
        group = self.course()
        route = f"/api/course-groups/{group['id']}/howdy-rosters"
        self.assertEqual(self.client.post(route, json={}).status_code, 403, "a write without the workspace guard is refused")
        started = self.client.post(route, json={}, headers=HEADERS)
        self.assertEqual(started.status_code, 201, started.json)
        self.assertEqual(FakeBrowser.started[-1]["plan"], {"code": "MATH 140", "term": "Fall", "year": 2026, "sections": [507, 508],
                                                          "label": "Fall 2026"})
        self.assertEqual(FakeBrowser.started[-1]["portal"], howdy_import.PORTAL)
        self.assertIsNone(FakeBrowser.started[-1]["netid"], "no saved sign-in: the instructor signs in on the page")
        self.assertEqual(self.client.get("/api/howdy/session-1").json["state"], "starting")
        self.assertEqual(self.client.post("/api/course-groups/nope/howdy-rosters", json={}, headers=HEADERS).status_code, 404)

    def test_a_term_howdy_has_no_code_for_is_refused_before_signing_in(self):
        group = self.course(code="MATH 151", term="Other", term_other="Maymester")
        answer = self.client.post(f"/api/course-groups/{group['id']}/howdy-rosters", json={}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400)
        self.assertIn("Fall, Spring or Summer", answer.json["error"])
        self.assertEqual(FakeBrowser.started, [])


if __name__ == "__main__":
    unittest.main()
