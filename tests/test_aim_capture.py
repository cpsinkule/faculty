"""AIM FORM CAPTURE (2026-10-15, AGENTS.md "ACCOMMODATIONS"): the AIM window records the structure of the form the
instructor opened (field names, labels, buttons) and never what is in it. Faculty clicks, types and submits nothing in
AIM. These checks never sign in to AIM or open the live AIM window: the browser run reads a local `set_content` page,
every other test uses fakes and `aim.example.test`."""
import json
import re
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import aim_import
from app import create_app
from rosters import connect
from test_storage import HEADERS

ROOT = Path(__file__).resolve().parent.parent
PORTAL = "https://aim.example.test/tamu/instructor"
NAMES = ["Doe, Jane", "Li Wu", "Pat O'Brien", "X"]  # "X" has no word of two letters: it never matches anything


def collapse(text):
    return " ".join(str(text).split())


# --- form_structure: the server keeps the form's shape only ---------------------------------------------------------

def control(i, **values):
    return {"tag": "INPUT", "type": "TEXT", "name": f"field{i}", "id": f"f{i}", "label": f"Field {i}", "required": False,
            "options": [], **values}


def button(i, **values):
    return {"tag": "BUTTON", "type": "Submit", "name": f"button{i}", "id": f"b{i}", "text": f"Button {i}", **values}


class CaptureStructureTests(unittest.TestCase):
    def test_structure_keeps_the_form_and_no_values(self):
        raw = {
            "url": "https://aim.example.test/tamu/instructor/Testing.aspx?Step=Exams&token=SECRET-QUERY#SECRET-FRAGMENT",
            "headings": ["  Schedule \n  an   exam ", "Step 2: Exam details"],
            "controls": [
                control(0, tag="INPUT", type="Text", name="ctl00$ExamDate", id="examDate", label="Exam\n   date ", required=1,
                        value="SECRET-VALUE", checked=True, selected=True, defaultValue="SECRET-DEFAULT"),
                control(1, tag="SELECT", type="select-one", name="ctl00$ExamType", id="examType", label="Exam type",
                        options=["  Quiz ", "Final"], value="SECRET-OPTION-VALUE", selectedIndex=1),
                control(2, tag="INPUT", type="HIDDEN", name="__VIEWSTATE", id="__VIEWSTATE", label="", value="SECRET-VIEWSTATE"),
            ],
            "buttons": [button(0, tag="A", type="", name="", id="lnkNext", text="  Next   step ", href="javascript:__doPostBack('SECRET-HREF','')")],
            "value": "SECRET-TOP-LEVEL", "html": "<form>SECRET-HTML</form>", "cookies": "SECRET-COOKIE",
        }
        found = aim_import.form_structure(raw, [])
        self.assertEqual(set(found), {"schema", "page", "headings", "controls", "buttons", "dropped"})
        self.assertEqual(found["schema"], 1)
        self.assertEqual(found["page"], "/tamu/instructor/Testing.aspx", "the path only: no scheme, host, query or fragment")
        self.assertEqual(found["headings"], ["Schedule an exam", "Step 2: Exam details"])
        self.assertEqual(found["controls"], [
            {"tag": "input", "type": "text", "name": "ctl00$ExamDate", "id": "examDate", "label": "Exam date", "required": True, "options": []},
            {"tag": "select", "type": "select-one", "name": "ctl00$ExamType", "id": "examType", "label": "Exam type", "required": False,
             "options": ["Quiz", "Final"]},
            {"tag": "input", "type": "hidden", "name": "__VIEWSTATE", "id": "__VIEWSTATE", "label": "", "required": False, "options": []},
        ])
        self.assertEqual(found["buttons"], [{"tag": "a", "type": "", "name": "", "id": "lnkNext", "text": "Next step"}])
        self.assertIs(found["controls"][0]["required"], True)
        self.assertIs(found["controls"][1]["required"], False)
        self.assertEqual(found["dropped"], 0)
        self.assertNotIn("SECRET", json.dumps(found), "values, unknown keys, the query and the fragment are never kept")

        # Bounds: 50 headings, 400 controls, 100 buttons, 300 options per control, 200 characters per string.
        many = {"url": PORTAL + "/Big.aspx",
                "headings": [f"Heading {i}" for i in range(60)],
                "controls": [control(0, tag="SELECT", label="a" * 500, name="  " + "n" * 450, options=[f"Option {i}" for i in range(350)])]
                + [control(i) for i in range(1, 450)],
                "buttons": [button(0, text="Book " + "b" * 500)] + [button(i) for i in range(1, 120)]}
        bounded = aim_import.form_structure(many, [])
        self.assertEqual(bounded["page"], "/tamu/instructor/Big.aspx")
        self.assertEqual(bounded["headings"], [f"Heading {i}" for i in range(50)])
        self.assertEqual(len(bounded["controls"]), 400)
        self.assertEqual([c["name"] for c in bounded["controls"][1:4]], ["field1", "field2", "field3"], "the first ones are kept, in order")
        self.assertEqual(len(bounded["buttons"]), 100)
        self.assertEqual(bounded["controls"][0]["options"], [f"Option {i}" for i in range(300)])
        self.assertEqual(bounded["controls"][0]["label"], "a" * 200)
        self.assertEqual(bounded["controls"][0]["name"], "n" * 200, "collapsed first, then cut")
        self.assertEqual(bounded["buttons"][0]["text"], ("Book " + "b" * 500)[:200])
        self.assertEqual(bounded["dropped"], 0, "bounds are not student data")

    def test_student_names_and_nine_digit_numbers_are_dropped_and_counted(self):
        raw = {
            "url": PORTAL + "/Testing.aspx",
            "headings": ["Schedule an exam", "Booking for JANE DOE", "Requests"],
            "controls": [
                control(0, tag="SELECT", name="ctl00$Exam", id="exam", label="Exam", options=[
                    "Midterm",
                    "Jane Doe - MATH 140",      # a name, first then last
                    "Final",
                    "UIN 123456789",            # nine digits
                    "Doe Jane",                 # a name, last then first
                    "Janet Doe",                # "janet" is not "jane": kept
                    "12345678",                 # eight digits: kept
                    "1234567890",               # ten digits hold nine consecutive ones
                    "979-845-1637",             # a phone number: no nine consecutive digits
                    "Exam for Wu, Li",
                    "X-ray lab",                # "X" is no name
                    "Pat O'Brien (MATH 140)",
                ]),
                control(1, label="UIN 987654321", name="uin", id="uin"),
                control(2, label="Student", name="ctl00$Student$123456789", id="student"),
                control(3, label="Room", name="room", id="li-wu-room"),
            ],
            "buttons": [button(0, text="Book Pat O'Brien"), button(1, text="Save")],
        }
        found = aim_import.form_structure(raw, NAMES)
        self.assertEqual(found["controls"][0]["options"], ["Midterm", "Final", "Janet Doe", "12345678", "979-845-1637", "X-ray lab"])
        self.assertEqual(found["headings"], ["Schedule an exam", "", "Requests"], "a heading is blanked, so the shape stays")
        self.assertEqual(found["controls"][1]["label"], "")
        self.assertEqual(found["controls"][1]["name"], "uin")
        self.assertEqual(found["controls"][2]["name"], "")
        self.assertEqual(found["controls"][2]["label"], "Student")
        self.assertEqual(found["controls"][3]["id"], "", "an id's words count too: li-wu-room holds Li Wu")
        self.assertEqual(found["controls"][3]["label"], "Room")
        self.assertEqual([b["text"] for b in found["buttons"]], ["", "Save"])
        self.assertEqual(len(found["controls"]), 4)
        # Six options, one heading, one label, one name, one id and one button text.
        self.assertEqual(found["dropped"], 11)
        dumped = json.dumps(found)
        for gone in (r"\bJane\b", r"\bJANE\b", "123456789", "987654321", "O'Brien", r"\bWu\b"):
            self.assertNotRegex(dumped, gone)

        nobody = aim_import.form_structure(raw, [])
        self.assertEqual(len(nobody["controls"][0]["options"]), 10, "with no names only the nine-digit options go")
        self.assertEqual(nobody["dropped"], 4)
        self.assertEqual(aim_import.form_structure(raw, ["X", "", "  ", "J."])["dropped"], 4, "names without a two-letter word are ignored")

    def test_the_scan_reads_no_values_in_a_real_browser(self):
        scan = aim_import.FORM_SCAN
        for forbidden in (".value", "innerText", ".checked", ".selected", "outerHTML", "innerHTML", "cookie"):
            self.assertNotIn(forbidden, scan, forbidden)
        page_html = """<!doctype html><html><body>
<h1>Schedule an exam</h1><h2>Step 2: Exam details</h2><h3>Times</h3><h4>Not a heading kept</h4>
<form method="post" action="./Testing.aspx">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="PREFILLED-HIDDEN-STATE">
<label for="examDate">Exam date</label><input type="text" name="ctl00$ExamDate" id="examDate" value="PREFILLED-DATE" required>
<label>Duration <input type="number" name="ctl00$Minutes" id="minutes" value="45123"></label>
<select name="ctl00$ExamType" id="examType" aria-label="Exam type">
  <option value="OPTVAL-ONE">Quiz</option><option value="OPTVAL-TWO" selected>Final</option><option value="OPTVAL-THREE">Midterm</option>
</select>
<textarea name="ctl00$Notes" id="notes" placeholder="Notes for DR">PREFILLED-NOTES</textarea>
<input type="checkbox" name="ctl00$Calculator" id="calc" value="CHECKBOX-VALUE" checked><label for="calc">Calculator allowed</label>
<input type="submit" name="ctl00$Book" id="book" value="Book exam">
<input type="image" name="ctl00$Go" id="go" alt="Go">
<input type="reset" name="ctl00$Clear" id="clear">
<button type="button" name="ctl00$Cancel" id="cancel">Cancel</button>
<a id="lnkNext" href="javascript:__doPostBack('ctl00$Next','')">Next step</a>
<a id="help" href="/help">Help</a>
</form></body></html>"""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(page_html)
                page.fill("#examDate", "TYPED-DATE-VALUE")
                page.fill("#notes", "TYPED-NOTES-VALUE")
                page.select_option("#examType", "OPTVAL-THREE")
                page.uncheck("#calc")
                raw = page.main_frame.evaluate(scan)
                url = page.url
            finally:
                browser.close()
        dumped = json.dumps(raw)
        for value in ("PREFILLED", "TYPED", "OPTVAL", "CHECKBOX-VALUE", "45123"):
            self.assertNotIn(value, dumped, value)
        self.assertEqual(raw["url"], url)
        self.assertEqual([collapse(h) for h in raw["headings"]], ["Schedule an exam", "Step 2: Exam details", "Times"])
        controls = {c["id"]: c for c in raw["controls"]}
        self.assertEqual(set(controls), {"__VIEWSTATE", "examDate", "minutes", "examType", "notes", "calc", "book", "go", "clear"},
                         "every input (hidden and button inputs included), select and textarea")
        for key in ("tag", "type", "name", "id", "label", "required", "options"):
            self.assertIn(key, controls["examDate"], key)
        self.assertEqual(controls["__VIEWSTATE"]["type"].lower(), "hidden", "hidden inputs are listed, by name and type")
        self.assertEqual(controls["__VIEWSTATE"]["name"], "__VIEWSTATE")
        self.assertEqual(collapse(controls["examDate"]["label"]), "Exam date")
        self.assertTrue(controls["examDate"]["required"])
        self.assertFalse(controls["minutes"]["required"])
        self.assertEqual(collapse(controls["minutes"]["label"]), "Duration")
        self.assertEqual(collapse(controls["examType"]["label"]), "Exam type", "aria-label when there is no <label>")
        self.assertEqual(controls["examType"]["tag"].lower(), "select")
        self.assertEqual([collapse(o) for o in controls["examType"]["options"]], ["Quiz", "Final", "Midterm"])
        self.assertEqual(controls["examDate"]["options"], [])
        self.assertEqual(controls["notes"]["tag"].lower(), "textarea")
        self.assertEqual(collapse(controls["notes"]["label"]), "Notes for DR", "placeholder when there is neither")
        self.assertEqual(collapse(controls["calc"]["label"]), "Calculator allowed")
        buttons = {b["id"]: b for b in raw["buttons"]}
        self.assertEqual(set(buttons), {"book", "go", "clear", "cancel", "lnkNext"}, "no ordinary link is a button")
        for key in ("tag", "type", "name", "id", "text"):
            self.assertIn(key, buttons["cancel"], key)
        self.assertEqual(collapse(buttons["cancel"]["text"]), "Cancel")
        self.assertEqual(buttons["cancel"]["name"], "ctl00$Cancel")
        self.assertEqual(buttons["lnkNext"]["tag"].lower(), "a")
        self.assertEqual(collapse(buttons["lnkNext"]["text"]), "Next step")
        self.assertEqual(buttons["go"]["type"].lower(), "image")
        # What the server keeps of this page has none of the values either.
        kept = aim_import.form_structure(raw, [])
        self.assertNotIn("PREFILLED", json.dumps(kept))
        self.assertEqual(next(c for c in kept["controls"] if c["id"] == "examType")["options"], ["Quiz", "Final", "Midterm"])


# --- the capture session (no browser: its thread does nothing) ------------------------------------------------------

class FakeFrame:
    def __init__(self, url, raw=None):
        self.url, self.raw, self.scripts = url, raw, []

    def evaluate(self, script, *args):
        self.scripts.append(script)
        if script == aim_import.FORM_SCAN:
            return self.raw
        return {"text": "Overview\nTesting", "inputs": []}  # SCAN: an AIM page


class FakePage:
    def __init__(self, url, raw=None):
        self.url, self.main_frame = url, FakeFrame(url, raw)
        self.frames = [self.main_frame]
        self.clicked = []

    def is_closed(self):
        return False

    def evaluate(self, script, *args):
        return self.main_frame.evaluate(script, *args)

    def __getattr__(self, name):  # anything that would click, type, fill, post or navigate
        if name in ("click", "fill", "type", "press", "goto", "select_option", "check", "dispatch_event", "mouse", "keyboard", "request"):
            raise AssertionError(f"Faculty must not use page.{name} while capturing AIM's form")
        raise AttributeError(name)


class CaptureSessionTests(unittest.TestCase):
    def setUp(self):
        # The browser thread starts nothing: no Chromium and no portal is ever opened here.
        patcher = patch.object(aim_import.AimBrowser, "_run", lambda self: None); patcher.start(); self.addCleanup(patcher.stop)
        self.exports = []
        patcher = patch.object(aim_import, "export_one", lambda *args, **kwargs: self.exports.append(args) or {"term": "", "name": "x.xlsx", "content": b"", "size": 0, "kind": "accommodations"})
        patcher.start(); self.addCleanup(patcher.stop)

    def browser(self, cls, page, **kwargs):
        made = cls(PORTAL, "jdoe", "hunter2", **kwargs)
        self.addCleanup(made.close)
        context = MagicMock()
        context.pages = [page]
        context.new_cdp_session.side_effect = RuntimeError("no screencast here")
        made._context = context
        return made

    def test_a_capture_session_never_exports(self):
        page = FakePage(PORTAL + "/Overview.aspx")
        capture = self.browser(aim_import.AimCaptureBrowser, page)
        self.assertIsInstance(capture, aim_import.AimBrowser)
        self.assertFalse(capture.show_page, "with credentials the page is not shown while signing in")
        for _ in range(3):
            capture._drive(page)
        self.assertEqual(capture.step, "aim")
        self.assertEqual(capture.state, "signed_in", "the instructor goes on in the live page")
        self.assertTrue(capture.show_page, "reaching AIM opens the live page")
        self.assertEqual(self.exports, [], "no export_one call")
        self.assertFalse(capture._closing.is_set(), "the browser stays open for the instructor")
        capture._on_aim(page.main_frame, "Overview", [], time.monotonic())
        self.assertEqual(self.exports, [])
        with self.assertRaises(ValueError) as caught:
            capture.export(["accommodations"])
        self.assertEqual(str(caught.exception), "This window records AIM's form; it does not export.")
        self.assertEqual(self.exports, [])
        status = capture.status()
        self.assertIs(status["capture"], True)
        self.assertEqual(status["state"], "signed_in")

        importing = self.browser(aim_import.AimBrowser, FakePage(PORTAL + "/Overview.aspx"))
        plain = importing.status()
        self.assertNotIn("capture", plain, "the import window's status is unchanged")
        self.assertEqual(set(status) - set(plain), {"capture"})
        importing._on_aim(None, "Overview", [], time.monotonic())
        self.assertEqual(len(self.exports), 2, "an import still exports both")

    def read_form(self, capture):
        """`form()` on a request thread; this thread plays the browser's, running what it queued."""
        outcome = {}

        def request():
            try:
                outcome["result"] = capture.form()
            except Exception as error:
                outcome["error"] = error
        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while thread.is_alive() and time.monotonic() < deadline:
            try:
                action, done = capture._commands.get(timeout=0.05)
            except Exception:
                continue
            if done is not None and not done.set_running_or_notify_cancel():
                continue
            try:
                result = action()
            except Exception as error:
                if done is not None:
                    done.set_exception(error)
                continue
            if done is not None:
                done.set_result(result)
        thread.join(5)
        self.assertFalse(thread.is_alive(), "form() returned")
        return outcome

    def test_the_form_is_read_only_from_an_aim_page_and_only_when_signed_in(self):
        raw = {"url": PORTAL + "/Testing.aspx?Step=Exams", "headings": ["Schedule"], "controls": [], "buttons": []}
        page = FakePage(PORTAL + "/Testing.aspx?Step=Exams", raw)
        capture = self.browser(aim_import.AimCaptureBrowser, page)
        with self.assertRaises(ValueError):
            capture.form()  # still starting
        capture._drive(page)
        self.assertEqual(capture.state, "signed_in")
        outcome = self.read_form(capture)
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["result"], raw)
        self.assertIn(aim_import.FORM_SCAN, page.main_frame.scripts)
        for elsewhere in ("https://login.microsoftonline.com/tenant/saml2", "https://aim.example.test/other/Testing.aspx",
                          "https://evil.example.test/tamu/instructor/Testing.aspx"):
            with self.subTest(url=elsewhere):
                other = FakePage(elsewhere, raw)
                capture._context.pages = [other]
                outcome = self.read_form(capture)
                self.assertIsInstance(outcome.get("error"), ValueError)
                self.assertEqual(str(outcome["error"]), "Open AIM's form in the page first.")
        self.assertEqual(self.exports, [])


# --- routes ---------------------------------------------------------------------------------------------------------

class FakeImport:
    """AimBrowser's surface for an import sign-in: no form, no capture."""
    made = []

    def __init__(self, portal, netid=None, password=None):
        self.id, self.portal, self.state, self.closed = f"import{len(FakeImport.made)}", portal, "signed_in", False
        self.given = [(netid, password)]
        FakeImport.made.append(self)

    def status(self):
        return {"id": self.id, "state": self.state, "step": "aim", "place": "Signed in to AIM", "message": "", "files": [], "problems": []}

    def watch(self):
        pass

    def close(self):
        self.closed = True

    def expired(self, now=None):
        return self.closed


class FakeCapture(FakeImport):
    made = []
    capture = True

    def __init__(self, portal, netid=None, password=None):
        self.id, self.portal, self.state, self.closed = f"capture{len(FakeCapture.made)}", portal, "signed_in", False
        self.given, self.raw, self.error, self.reads = [(netid, password)], None, None, 0
        FakeCapture.made.append(self)

    def status(self):
        return {**super().status(), "capture": True}

    def form(self):
        self.reads += 1
        if self.error:
            raise ValueError(self.error)
        return self.raw

    def export(self, kinds):
        raise ValueError("This window records AIM's form; it does not export.")


class FakeCanvas:
    made = []
    ENDED = "This Canvas sign-in has ended."

    def __init__(self, portal, netid=None, password=None):
        self.id, self.state, self.closed = f"canvas{len(FakeCanvas.made)}", "signing_in", False
        FakeCanvas.made.append(self)

    def status(self):
        return {"id": self.id, "state": self.state}

    def watch(self):
        pass

    def close(self):
        self.closed = True

    def expired(self, now=None):
        return self.closed


def raw_form(page="Testing.aspx", heading="Schedule an exam"):
    return {
        "url": f"{PORTAL}/{page}?Step=Exams&student=123456789",
        "headings": [heading, "Booking for Jane Doe"],
        "controls": [
            {"tag": "SELECT", "type": "select-one", "name": "ctl00$Exam", "id": "exam", "label": "Exam", "required": True,
             "options": ["Midterm", "Doe, Jane (MATH 140)", "Final", "UIN 123456789"], "value": "SECRET-SELECTED"},
            {"tag": "INPUT", "type": "text", "name": "ctl00$Date", "id": "date", "label": "Exam date", "required": False,
             "options": [], "value": "SECRET-TYPED"},
            {"tag": "INPUT", "type": "hidden", "name": "__VIEWSTATE", "id": "__VIEWSTATE", "label": "", "required": False,
             "options": [], "value": "SECRET-VIEWSTATE"},
        ],
        "buttons": [{"tag": "INPUT", "type": "submit", "name": "ctl00$Book", "id": "book", "text": "Book exam"}],
    }


STUDENTS = {"groups": [{"term_id": "t1", "code": "MATH 140", "term_label": "Fall 2026",
                        "students": [{"enrollment_id": "e1", "section_id": "s1", "name": "Doe, Jane", "sections": ["507"], "dropped": False},
                                     {"enrollment_id": "e2", "section_id": "s1", "name": "Li Wu", "sections": ["507"], "dropped": True}]}]}
STUDENT_NAMES = ["Doe, Jane", "Li Wu"]


class CaptureRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        for name, fake in (("AimBrowser", FakeImport), ("AimCaptureBrowser", FakeCapture)):
            patcher = patch.object(aim_import, name, fake); patcher.start(); self.addCleanup(patcher.stop)
        import canvas.signin
        patcher = patch.object(canvas.signin, "CanvasBrowser", FakeCanvas); patcher.start(); self.addCleanup(patcher.stop)
        FakeImport.made, FakeCapture.made, FakeCanvas.made = [], [], []
        self.app = create_app(self.temp.name); self.app.testing = True
        self.app.config["AIM_PORTAL"] = PORTAL
        patcher = patch.object(self.app.extensions["student_notes"], "students", lambda *args, **kwargs: STUDENTS)
        patcher.start(); self.addCleanup(patcher.stop)
        self.inbox = self.app.extensions["inbox"]
        self.client = self.app.test_client(); self.client.get("/").close()

    def start_capture(self, **body):
        started = self.client.post("/api/accommodations/aim", json={"capture": True, **body}, headers=HEADERS)
        self.assertEqual(started.status_code, 201, started.get_data(as_text=True))
        self.assertIs(started.json["capture"], True)
        return started.json["id"], f"/api/accommodations/aim/{started.json['id']}"

    def job_of(self, artifact_id):
        with connect(self.inbox.path) as db:
            row = db.execute("SELECT job_id FROM inbox_artifacts WHERE id=?", (artifact_id,)).fetchone()
        self.assertIsNotNone(row, "the capture is an Inbox artifact")
        return self.inbox.get(row["job_id"])

    def test_record_keeps_a_capture_in_the_inbox_and_lists_it(self):
        self.assertEqual(self.client.get("/api/accommodations/aim/captures").json, {"captures": []})
        _, base = self.start_capture(netid="jdoe", password="hunter2")
        session = FakeCapture.made[0]
        self.assertEqual((session.portal, session.given), (PORTAL, [("jdoe", "hunter2")]))
        self.assertEqual(FakeImport.made, [], "a capture start makes no import browser")
        session.raw = raw_form()
        recorded = self.client.post(base + "/record", json={}, headers=HEADERS)
        self.assertEqual(recorded.status_code, 201, recorded.get_data(as_text=True))
        first = recorded.json
        self.assertEqual(set(first), {"id", "name", "page", "controls", "buttons", "dropped", "received_at", "download"})
        self.assertRegex(first["name"], r"^aim-form-\d{8}-\d{6}\.json$")
        self.assertEqual(first["page"], "/tamu/instructor/Testing.aspx")
        self.assertEqual((first["controls"], first["buttons"], first["dropped"]), (3, 1, 3))
        self.assertEqual(first["download"], f"/api/inbox/artifacts/{first['id']}/download")
        answer = recorded.get_data(as_text=True)
        for secret in ("SECRET", "123456789", "Jane", "Step=Exams", "hunter2"):
            self.assertNotIn(secret, answer)

        # The kept file is exactly the structure, with the current rosters' names filtered out.
        download = self.client.get(first["download"])
        self.assertEqual(download.status_code, 200)
        kept = json.loads(download.get_data().decode("utf-8"))
        self.assertEqual(kept, aim_import.form_structure(raw_form(), STUDENT_NAMES))
        self.assertEqual(kept["controls"][0]["options"], ["Midterm", "Final"])
        self.assertEqual(kept["headings"], ["Schedule an exam", ""])
        for secret in ("SECRET", "123456789", "Jane", "Doe", "Step=Exams", "aim.example.test"):
            self.assertNotIn(secret, download.get_data(as_text=True))

        # An owner-less workspace Inbox job of its own action.
        job = self.job_of(first["id"])
        self.assertEqual(job["action"], "aim.form_capture")
        self.assertEqual(job["scope_label"], "AIM form capture")
        self.assertEqual(job["source"], "web")
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["parameters"]["metadata"], {"page": "/tamu/instructor/Testing.aspx", "controls": 3, "buttons": 1, "dropped": 3})
        self.assertEqual([a["filename"] for a in job["artifacts"] if a["role"] == "input"], [first["name"]])

        # A second form, later: listed first.
        session.raw = raw_form("Exams/Schedule.aspx", heading="Pick a time")
        second = self.client.post(base + "/record", json={}, headers=HEADERS)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(second.json["id"], first["id"])
        listed = self.client.get("/api/accommodations/aim/captures")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json, {"captures": [second.json, first]})

        # The worker completes them and changes nothing else.
        while self.inbox.process_one():
            pass
        job = self.job_of(first["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(job["result"].get("message"), "AIM form structure kept.")
        self.assertEqual(self.client.get("/api/accommodations/aim/captures").json["captures"], [second.json, first])

        # Deleted in the Inbox: no longer listed (PRESENT); restored: listed again.
        self.inbox.remove(job["id"], job["version"], "delete")
        self.assertEqual(self.client.get("/api/accommodations/aim/captures").json["captures"], [second.json])
        self.inbox.restore(job["id"])
        self.assertEqual([c["id"] for c in self.client.get("/api/accommodations/aim/captures").json["captures"]], [second.json["id"], first["id"]])

        # form() refusing is a 400 with its words; an ended session is a 410.
        session.error = "Open AIM's form in the page first."
        refused = self.client.post(base + "/record", json={}, headers=HEADERS)
        self.assertEqual((refused.status_code, refused.json.get("error")), (400, "Open AIM's form in the page first."))
        self.assertEqual(len(self.client.get("/api/accommodations/aim/captures").json["captures"]), 2, "nothing kept")
        self.assertEqual(self.client.post(base + "/close", json={}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.post(base + "/record", json={}, headers=HEADERS).status_code, 410)

    def test_record_refuses_outside_a_capture_session(self):
        started = self.client.post("/api/accommodations/aim", json={"netid": "jdoe", "password": "hunter2"}, headers=HEADERS)
        self.assertEqual(started.status_code, 201)
        self.assertNotIn("capture", started.json)
        self.assertEqual((len(FakeImport.made), FakeCapture.made), (1, []))
        refused = self.client.post(f"/api/accommodations/aim/{started.json['id']}/record", json={}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json.get("error"), "This window is not recording AIM's form.")
        # Only a JSON true starts a capture.
        self.client.post("/api/accommodations/aim", json={"capture": "true"}, headers=HEADERS)
        self.assertEqual((len(FakeImport.made), FakeCapture.made), (2, []))
        # One AIM session at a time: a capture ends the import sign-in, and an import ends the capture.
        capture_id, _ = self.start_capture(saved=False)
        self.assertTrue(FakeImport.made[1].closed)
        self.assertEqual(self.client.get(f"/api/accommodations/aim/{started.json['id']}").status_code, 410)
        self.client.post("/api/accommodations/aim", json={}, headers=HEADERS)
        self.assertTrue(FakeCapture.made[0].closed)
        self.assertEqual(self.client.get(f"/api/accommodations/aim/{capture_id}").status_code, 410)
        # Canvas's sign-in has no capture: refused before any browser is made.
        canvas = self.client.post("/api/canvas/signin", json={"capture": True}, headers=HEADERS)
        self.assertEqual(canvas.status_code, 400)
        self.assertEqual(FakeCanvas.made, [])
        self.assertEqual(self.client.post("/api/canvas/signin", json={}, headers=HEADERS).status_code, 201, "a plain Canvas start still works")
        self.assertEqual(len(FakeCanvas.made), 1)
        self.assertEqual(self.client.get("/api/accommodations/aim/captures").json, {"captures": []})

    def test_sessions_without_a_capture_class_refuse_before_ending_the_current_one(self):
        sessions = aim_import.AimSessions(FakeImport)
        current = sessions.start(PORTAL, "jdoe", "pw")
        with self.assertRaises(ValueError):
            sessions.start(PORTAL, capture=True)
        self.assertFalse(current.closed)
        self.assertIs(sessions.get(current.id), current)
        self.assertEqual(len(FakeImport.made), 1)
        both = aim_import.AimSessions(FakeImport, capture=FakeCapture)
        made = both.start(PORTAL, None, None, capture=True)
        self.assertIsInstance(made, FakeCapture)
        self.assertIsInstance(both.start(PORTAL), FakeImport)
        self.assertTrue(made.closed)

    def test_capture_routes_keep_the_guard_and_the_assistant_out(self):
        from assistant import policy
        self.assertEqual(self.client.post("/api/accommodations/aim", json={"capture": True}).status_code, 403)
        self.assertEqual(FakeCapture.made, [])
        _, base = self.start_capture()
        FakeCapture.made[0].raw = raw_form()
        self.assertEqual(self.client.post(base + "/record", json={}).status_code, 403)
        self.assertEqual(FakeCapture.made[0].reads, 0, "a refused request reads nothing")
        rules = {(rule.rule, method): rule.endpoint for rule in self.app.url_map.iter_rules() for method in rule.methods}
        self.assertEqual(rules[("/api/accommodations/aim/<session_id>/record", "POST")], "accommodations.aim_record")
        self.assertEqual(rules[("/api/accommodations/aim/captures", "GET")], "accommodations.aim_captures")
        rule = policy.base_rule("accommodations.aim_record")
        self.assertEqual((rule.tier, rule.reason), ("never", "AIM sign-in window"))
        self.assertIn("accommodations.aim_captures", policy.GET_BLOCKED)
        self.assertEqual(self.client.get("/api/accommodations/aim/captures").status_code, 200, "the static route wins over a session id")


# --- the Inbox action -----------------------------------------------------------------------------------------------

class CaptureInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.inbox = self.app.extensions["inbox"]

    def submit(self, files, key):
        return self.inbox.submit({"action": "aim.form_capture", "metadata": {"page": "/x", "controls": 0, "buttons": 0, "dropped": 0}},
                                 files, source="web", idempotency_key=key)

    def test_the_action_is_an_owner_less_workspace_action(self):
        from teach_inbox.handlers import HANDLERS
        from teach_inbox.protocol import ACTIONS
        self.assertEqual(ACTIONS["aim.form_capture"], {"label": "Keep AIM's form structure", "scopes": ["workspace"]})
        self.assertIn("aim.form_capture", HANDLERS)
        content = json.dumps({"schema": 1}).encode()
        good = self.submit([{"filename": "aim-form-20261015-090000.json", "content": content}], "one")
        self.assertEqual((good["status"], good["scope_label"]), ("pending", "AIM form capture"))
        for key, files in (("two", [{"filename": "a.json", "content": content}, {"filename": "b.json", "content": content + b" "}]),
                           ("text", [{"filename": "a.txt", "content": content + b"  "}]),
                           ("none", [])):
            with self.subTest(key=key):
                self.assertEqual(self.submit(files, key)["status"], "needs_review", "exactly one .json file")
        with self.assertRaises(ValueError):
            self.inbox.create_code({"action": "aim.form_capture", "metadata": {}}, "Capture", 24)
        self.assertTrue(self.inbox.process_one())
        done = self.inbox.get(good["id"])
        self.assertEqual(done["status"], "completed", done.get("error"))
        self.assertEqual(done["result"].get("message"), "AIM form structure kept.")


# --- the window (static checks; AC9's look is checked by hand) ------------------------------------------------------

class CaptureWindowTests(unittest.TestCase):
    def test_the_window_and_the_page_offer_the_capture(self):
        window = (ROOT / "static" / "aim-signin.js").read_text()
        page = (ROOT / "static" / "accommodations_page.js").read_text()
        self.assertIsNotNone(re.search(r"export function openAimCapture\s*\(", window), "aim-signin.js exports openAimCapture(options)")
        for words in ("Capture AIM's form", "Record this form", "data-aim-record", "data-aim-captures", "Recorded forms",
                      "No form recorded yet.", "/api/accommodations/aim/captures", "capture: true",
                      "Signed in to AIM. Open the form in the page below, then choose Record this form.",
                      "Faculty keeps the form's fields and buttons only, never what is in them."):
            self.assertTrue(words in window, f"aim-signin.js says {words!r}")
        self.assertIn("data-aim-capture", page)
        self.assertIn("openAimCapture", page)
        actions = re.search(r'class="accommodation-actions">(.*?)</div>', page)
        self.assertIsNotNone(actions)
        self.assertLess(actions.group(1).index("data-aim-import"), actions.group(1).index("data-aim-capture"),
                        "Capture AIM's form comes right after Import from AIM")


if __name__ == "__main__":
    unittest.main()
