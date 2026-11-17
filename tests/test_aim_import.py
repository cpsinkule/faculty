"""Import from AIM without a browser: reading AIM's ASP.NET form, the postback, the export checks, the window's input and
the routes. AIM here is synthetic markup written in this file."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import aim_import
from aim_import import (PREFIX, Refused, check_answer, check_credentials, check_events, classify, duo_code, error_line, export_one,
                        filename, netid_for, page_term, parse_form, postback_body)
from app import create_app
from test_storage import HEADERS

BASE = "https://cascade.accessiblelearning.com/tamu/instructor/"
BUTTON = PREFIX + "PnlExport$BtnExport"
PAGE = f"""<html><body><p>Previous Term <span>Term:&nbsp;Fall 2026</span> Next Term</p>
<form method="post" action="./Overview.aspx" id="aspnetForm">
<input type="hidden" name="__EVENTTARGET" value=""><input type="hidden" name="__EVENTARGUMENT" value="">
<input type="hidden" name="__VIEWSTATE" value="abc+/=">
<select name="{PREFIX}PnlExport$DDLCancelled$DDLInput"><option value="0">Do Not Include Cancelled Requests<option value="1">Include All Requests</select>
<select name="picked"><option value="a">A</option><option value="b" selected>B</option></select>
<select name="worded"><option selected>  Two   words </option></select>
<input type="text" name="search" value="MATH &amp; more"><input type="text" name="off" value="x" disabled>
<input type="checkbox" name="ticked" checked><input type="checkbox" name="unticked" value="1">
<input type="radio" name="choice" value="r1"><input type="radio" name="choice" value="r2" checked>
<input type="file" name="upload"><input type="submit" name="go" value="Go"><input type="image" name="pic">
<textarea name="note">line one</textarea>
<button type="button" name="{BUTTON}" onclick="__doPostBack('{BUTTON}','')">Export</button>
</form><form action="/elsewhere"><input name="second" value="no"></form></body></html>"""
XLSX_BYTES = b"PK\x03\x04" + b"spreadsheet" * 10


class FakeResponse:
    def __init__(self, url, status=200, text="", body=b"", headers=None):
        self.url, self.status, self._text, self._body, self.headers = url, status, text, body, headers or {}

    @property
    def ok(self):
        return 200 <= self.status < 300

    def text(self):
        return self._text

    def body(self):
        return self._body


class FakeClient:
    """AIM as the signed-in browser's request context sees it."""

    def __init__(self, page=PAGE, landed=None, answer=None):
        self.page, self.landed, self.posts = page, landed, []
        self.answer = answer or FakeResponse(BASE + "Overview.aspx", body=XLSX_BYTES,
                                             headers={"content-disposition": "attachment; filename=Accommodation_Request_20260926.xlsx"})

    def get(self, url, **_):
        return FakeResponse(self.landed or url, text=self.page)

    def post(self, url, data=None, headers=None, max_redirects=None, **_):
        self.posts.append({"url": url, "data": parse_qs(data, keep_blank_values=True), "headers": headers, "max_redirects": max_redirects})
        return self.answer


class FormTests(unittest.TestCase):
    def test_the_first_form_gives_what_a_browser_would_submit(self):
        form = parse_form(PAGE)
        self.assertEqual(form.action, "./Overview.aspx")
        fields = dict(form.fields)
        self.assertEqual(fields["__VIEWSTATE"], "abc+/=")
        self.assertEqual(fields[PREFIX + "PnlExport$DDLCancelled$DDLInput"], "0", "an unselected select submits its first option")
        self.assertEqual(fields["picked"], "b")
        self.assertEqual(fields["worded"], "Two words")
        self.assertEqual(fields["search"], "MATH & more")
        self.assertEqual(fields["ticked"], "on")
        self.assertEqual(fields["choice"], "r2")
        self.assertEqual(fields["note"], "line one")
        for absent in ("off", "unticked", "upload", "go", "pic", BUTTON, "second"):
            self.assertNotIn(absent, fields)
        self.assertIn(BUTTON, form.names)
        self.assertNotIn("second", form.names)

    def test_the_postback_names_its_button_once(self):
        body = parse_qs(postback_body(parse_form(PAGE).fields, BUTTON), keep_blank_values=True)
        self.assertEqual(body["__EVENTTARGET"], [BUTTON])
        self.assertEqual(body["__EVENTARGUMENT"], [""])
        self.assertEqual(body["__VIEWSTATE"], ["abc+/="])

    def test_term_and_file_names(self):
        self.assertEqual(page_term(PAGE), "Fall 2026")
        self.assertEqual(page_term("<p>No term here</p>"), "")
        self.assertEqual(filename("attachment; filename=Exams_20260926.xlsx", "exams"), "Exams_20260926.xlsx")
        self.assertEqual(filename('attachment; filename="A B.xlsx"', "exams"), "A B.xlsx")
        self.assertEqual(filename("attachment; filename*=UTF-8''Req%20uests.xlsx", "exams"), "Req uests.xlsx")
        self.assertEqual(filename("attachment; filename=../../etc/x.xlsx", "exams"), "x.xlsx")
        self.assertEqual(filename("attachment; filename=report.csv", "exams"), "aim-exams.xlsx")
        self.assertEqual(filename(None, "accommodations"), "aim-accommodations.xlsx")


class ExportTests(unittest.TestCase):
    def test_an_export_posts_the_page_back_to_aim_and_returns_the_spreadsheet(self):
        client = FakeClient()
        found = export_one(client, BASE, "accommodations")
        self.assertEqual((found["name"], found["content"], found["term"]), ("Accommodation_Request_20260926.xlsx", XLSX_BYTES, "Fall 2026"))
        post = client.posts[0]
        self.assertEqual(post["url"], BASE + "Overview.aspx")
        self.assertEqual(post["data"]["__EVENTTARGET"], [BUTTON])
        self.assertNotIn("upload", post["data"])
        self.assertEqual(post["max_redirects"], 0)
        self.assertEqual(post["headers"]["Origin"], "https://cascade.accessiblelearning.com")

    def test_what_aim_did_not_give_is_named_and_nothing_else_is_sent(self):
        cases = [
            (FakeClient(landed="https://login.microsoftonline.com/x/saml2"), "sign-in has ended"),
            (FakeClient(landed=BASE + "Login.aspx"), "access policy"),
            (FakeClient(page=PAGE.replace(BUTTON, "something else")), "no Export button"),
            (FakeClient(page=PAGE.replace('action="./Overview.aspx"', 'action="https://example.test/steal"')), "somewhere else"),
            (FakeClient(page=PAGE.replace('action="./Overview.aspx"', 'action="http://cascade.accessiblelearning.com/tamu/instructor/Overview.aspx"')), "somewhere else"),
            (FakeClient(landed=BASE.replace('https:', 'http:') + 'Overview.aspx'), "sign-in has ended"),
            (FakeClient(answer=FakeResponse(BASE + "Overview.aspx", text="<html>error</html>", body=b"<html>error</html>")), "did not return a spreadsheet"),
            (FakeClient(answer=FakeResponse(BASE + "Overview.aspx", status=302, body=b"")), "did not return a spreadsheet"),
            (FakeClient(answer=FakeResponse(BASE + "Overview.aspx", body=b"PK\x03\x04" + b"x" * aim_import.LIMIT)), "10 MB"),
        ]
        for client, words in cases:
            with self.subTest(words=words), self.assertRaises(Refused) as caught:
                export_one(client, BASE, "accommodations")
            self.assertIn(words, str(caught.exception))
            if words in ("sign-in has ended", "access policy", "no Export button", "somewhere else"):
                self.assertEqual(client.posts, [], "nothing is posted when the page is not AIM's own export page")

    def test_the_dr_export_reads_the_exams_step(self):
        page = PAGE.replace(BUTTON, PREFIX + "GFormUpload$BtnExportData").replace("./Overview.aspx", "./Testing.aspx?Step=Exams")
        client = FakeClient(page=page)
        found = export_one(client, BASE, "exams")
        self.assertEqual(client.posts[0]["url"], BASE + "Testing.aspx?Step=Exams")
        self.assertEqual(found["term"], "")


class InputTests(unittest.TestCase):
    def test_input_is_checked_and_never_repeated_in_an_error(self):
        events = check_events({"events": [{"type": "click", "x": 10, "y": 20}, {"type": "text", "text": "NetID@tamu.edu"},
                                          {"type": "key", "key": "Shift+Tab"}, {"type": "wheel", "x": 1, "y": 1, "dx": 0, "dy": 300},
                                          {"type": "back"}, {"type": "home"}]})
        self.assertEqual([event["type"] for event in events], ["click", "text", "key", "wheel", "back", "home"])
        secret = "hunter2\nsecret"
        for bad in ({"events": []}, {"events": [{"type": "click", "x": 5000, "y": 1}]}, {"events": [{"type": "click", "x": True, "y": 1}]},
                    {"events": [{"type": "key", "key": "Meta+q"}]}, {"events": [{"type": "text", "text": secret}]},
                    {"events": [{"type": "key", "key": {}}]}, {"events": [{"type": "key", "key": []}]},
                    {"events": [{"type": "text", "text": "x" * 2001}]}, {"events": [{"type": "eval"}]}, None, {"events": [{}] * 51}):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError) as caught:
                check_events(bad)
            self.assertNotIn("hunter2", str(caught.exception))


class StepTests(unittest.TestCase):
    """Sign-in pages as the driver reads them: address, visible text and visible fields (never values)."""

    def test_each_step_is_recognized_from_the_page(self):
        ms, duo = "https://login.microsoftonline.com/tenant/saml2", "https://api-1234.duosecurity.com/frame/v4/auth/prompt"
        email, password = [{"type": "email", "name": "loginfmt"}], [{"type": "password", "name": "passwd"}]
        cases = [
            (ms, "Sign in\nNetID@tamu.edu\nNext", email, "netid"),
            (ms, "Enter password\nForgot my password", password, "password"),
            (ms, "Stay signed in?\nDo this to reduce the number of times you are asked to sign in.\nNo\nYes", [], "stay_signed_in"),
            # TAMU's page before Duo, whose old password field a scan once counted (2026-09-26).
            (ms, "instructor@tamu.edu\nVerification Required\nYou will be redirected to Duo to verify your identity\nContinue", password, "duo_redirect"),
            (ms, "Working on it...", [], "working"),
            (ms, "More information required", [], "unknown"),
            ("https://gateway.tamu.edu/login", "NetID\nPassword\nLogin", [{"type": "text", "name": "username"}, {"type": "password", "name": "password"}], "password"),
            (duo, "Enter code in Duo Mobile\n482\nVerify it's you\nOther options", [], "duo_code"),
            (duo, "Check for a Duo Push\nSent to iOS\nOther options", [], "duo_push"),
            (duo, "Push timed out\nTry again\nOther options", [], "duo_problem"),
            (duo, "Is this your device?\nYes, this is my device\nNo, other people use this device", [], "duo_trust"),
            (duo, "Enter a passcode\nVerify", [{"type": "text", "name": "passcode-input"}], "duo_passcode"),
            (duo, "Other options\nDuo Push\nDuo Mobile passcode", [], "duo_choose"),
            (duo, "Success! Logging you in...", [], "working"),
            ("http://127.0.0.1:9/duo", "Secured by Duo\nEnter code in Duo Mobile\n915", [], "duo_code"),
            (BASE + "Login.aspx", "ACCESS POLICY\nCONTINUE TO VIEW STUDENT ACCOMMODATIONS", [], "policy"),
            (BASE.replace("/tamu/", "/TAMU/") + "Overview.aspx", "Overview", [], "aim"),
            ("https://cascade.accessiblelearning.com/tamu/elsewhere.html", "Something", [], "unknown"),
        ]
        for url, text, inputs, step in cases:
            with self.subTest(step=step, text=text[:30]):
                self.assertEqual(classify(url, text, inputs, BASE), step)

    def test_codes_complaints_and_netids(self):
        self.assertEqual(duo_code("Enter code in Duo Mobile\n 482 \nOther options"), "482")
        self.assertEqual(duo_code("Sent to iOS (XXX-XXX-1234)"), "")
        self.assertEqual(error_line("Enter password\nYour account or password is incorrect. If you don't remember..."),
                         "Your account or password is incorrect. If you don't remember...")
        self.assertEqual(error_line("Enter password"), "")
        self.assertEqual(netid_for("jdoe", [{"type": "email", "name": "loginfmt"}]), "jdoe@tamu.edu")
        self.assertEqual(netid_for("jdoe@tamu.edu", [{"type": "email"}]), "jdoe@tamu.edu")
        self.assertEqual(netid_for("jdoe@tamu.edu", [{"type": "text", "name": "username"}]), "jdoe")

    def test_credentials_and_answers_are_checked_without_repeating_them(self):
        self.assertEqual(check_credentials({"netid": " jdoe ", "password": "pa ss"}), {"netid": "jdoe", "password": "pa ss"})
        self.assertEqual(check_credentials({"password": "x"}, need_netid=False), {"netid": None, "password": "x"})
        for bad in ({"password": "hunter2"}, {"netid": "jdoe"}, {"netid": "jdoe", "password": "hunter2\n"}, {"netid": "jdoe", "password": "h" * 300}, None):
            with self.subTest(bad=str(bad)[:20]), self.assertRaises(ValueError) as caught:
                check_credentials(bad)
            self.assertNotIn("hunter2", str(caught.exception))
        self.assertEqual(check_answer({"action": "resend"}), "resend")
        self.assertEqual(check_answer({"action": "passcode", "code": " 123456 "}), ("passcode", "123456"))
        for bad in ({"action": "passcode", "code": "12ab56"}, {"action": "passcode", "code": "123"}, {"action": "approve"}, None):
            with self.subTest(bad=str(bad)), self.assertRaises(ValueError):
                check_answer(bad)


class SavedSignInTests(unittest.TestCase):
    """The saved Gradescope sign-in doubles as the NetID sign-in; read with that file's own checks, never echoed."""

    def saved(self, content, mode=0o600):
        folder = tempfile.mkdtemp(); self.addCleanup(lambda: __import__("shutil").rmtree(folder))
        path = os.path.join(folder, "gradescope.json")
        with open(path, "w") as handle:
            handle.write(content)
        os.chmod(path, mode)
        return path

    def test_the_saved_sign_in_is_read_only_from_a_private_file(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(aim_import.SAVED_ENV, None)
            self.assertIsNone(aim_import.saved_sign_in())
        good = self.saved(json.dumps({"email": "jdoe@tamu.edu", "password": "hunter2"}))
        with patch.dict(os.environ, {aim_import.SAVED_ENV: good}):
            self.assertEqual(aim_import.saved_sign_in(), ("jdoe@tamu.edu", "hunter2"))
        for path in (self.saved(json.dumps({"email": "jdoe@tamu.edu", "password": "hunter2"}), mode=0o644),
                     self.saved("not json hunter2"), self.saved(json.dumps({"email": "jdoe@tamu.edu"}))):
            with self.subTest(path=path), patch.dict(os.environ, {aim_import.SAVED_ENV: path}), self.assertRaises(ValueError) as caught:
                aim_import.saved_sign_in()
            self.assertNotIn("hunter2", str(caught.exception))


class FakeBrowser:
    """AimBrowser's surface without a browser or a thread."""
    made = []

    def __init__(self, portal, netid=None, password=None):
        self.id, self.portal, self.state, self.inputs, self.closed = f"session{len(FakeBrowser.made)}", portal, "signed_in", [], False
        self.given, self.answers, self.shown = [(netid, password)], [], False
        FakeBrowser.made.append(self)

    def status(self):
        return {"id": self.id, "state": self.state, "place": "Signed in to AIM", "frame": 1, "width": 1100, "height": 760, "files": [], "problems": [], "term": "", "message": ""}

    def watch(self):
        pass

    def picture(self, after=None):
        return 1, b"\xff\xd8jpeg"

    def send(self, events):
        self.inputs.extend(events)

    def credentials(self, netid, password):
        self.given.append((netid, password))

    def answer(self, intent):
        self.answers.append(intent)

    def show(self):
        self.shown = True

    def export(self, kinds):
        return {"term": "Fall 2026", "problems": [], "files": [{"kind": kind, "name": f"{kind}.xlsx", "size": 3} for kind in kinds]}

    def file(self, kind):
        return {"kind": kind, "name": f"{kind}.xlsx", "size": 3, "content": b"PK\x03\x04"}

    def close(self):
        self.closed = True

    def expired(self):
        return self.closed


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        patcher = patch.object(aim_import, "AimBrowser", FakeBrowser); patcher.start(); self.addCleanup(patcher.stop)
        FakeBrowser.made = []
        self.app = create_app(self.temp.name); self.app.testing = True
        self.app.config["AIM_PORTAL"] = "https://aim.example.test/tamu/instructor"
        self.client = self.app.test_client(); self.client.get("/").close()

    def test_one_sign_in_at_a_time_through_its_routes(self):
        started = self.client.post("/api/accommodations/aim", json={"netid": "jdoe", "password": "hunter2"}, headers=HEADERS)
        self.assertEqual(started.status_code, 201)
        self.assertNotIn("hunter2", started.get_data(as_text=True))
        base = f"/api/accommodations/aim/{started.json['id']}"
        self.assertEqual(FakeBrowser.made[0].portal, "https://aim.example.test/tamu/instructor")
        self.assertEqual(FakeBrowser.made[0].given, [("jdoe", "hunter2")])
        refused = self.client.post(base + "/credentials", json={"password": "hunter2\n"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400); self.assertNotIn("hunter2", refused.get_data(as_text=True))
        self.assertEqual(self.client.post(base + "/credentials", json={"password": "second"}, headers=HEADERS).status_code, 202)
        self.assertEqual(FakeBrowser.made[0].given[-1], (None, "second"))
        self.assertEqual(self.client.post(base + "/answer", json={"action": "passcode", "code": "123456"}, headers=HEADERS).status_code, 202)
        self.assertEqual(self.client.post(base + "/answer", json={"action": "passcode", "code": "12"}, headers=HEADERS).status_code, 400)
        self.assertEqual(FakeBrowser.made[0].answers, [("passcode", "123456")])
        self.assertEqual(self.client.post(base + "/show", json={}, headers=HEADERS).json, {"shown": True})
        self.assertTrue(FakeBrowser.made[0].shown)
        # Starting without credentials is signing in on the page itself.
        self.assertEqual(self.client.post("/api/accommodations/aim", json={"netid": "jdoe"}, headers=HEADERS).status_code, 400)
        self.assertEqual(len(FakeBrowser.made), 1, "a refused start makes no browser")
        self.assertEqual(self.client.get(base).json["state"], "signed_in")
        frame = self.client.get(base + "/frame?after=0")
        self.assertEqual((frame.mimetype, frame.headers["Cache-Control"], frame.headers["X-Aim-Frame"]), ("image/jpeg", "no-store", "1"))
        self.assertEqual(self.client.get(base + "/frame?after=1").status_code, 204, "no newer picture yet")
        self.assertEqual(self.client.post(base + "/input", json={"events": [{"type": "text", "text": "abc"}]}, headers=HEADERS).status_code, 202)
        self.assertEqual(FakeBrowser.made[0].inputs, [{"type": "text", "text": "abc"}])
        self.assertEqual(self.client.post(base + "/input", json={"events": [{"type": "key", "key": "F12"}]}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post(base + "/export", json={"kinds": ["grades"]}, headers=HEADERS).status_code, 400)
        for kinds in ([{}], [[]], ['exams', {}]):
            self.assertEqual(self.client.post(base + "/export", json={"kinds": kinds}, headers=HEADERS).status_code, 400)
        exported = self.client.post(base + "/export", json={"kinds": ["accommodations", "exams"]}, headers=HEADERS)
        self.assertEqual([item["kind"] for item in exported.json["files"]], ["accommodations", "exams"])
        file = self.client.get(base + "/files/exams")
        self.assertEqual((file.mimetype, file.headers["Cache-Control"]), (aim_import.XLSX, "no-store"))
        self.assertIn('filename="exams.xlsx"', file.headers["Content-Disposition"])
        # A second sign-in ends the first; a closed one answers 410 (not 404/409, which refresh the whole app).
        second = self.client.post("/api/accommodations/aim", json={}, headers=HEADERS).json["id"]
        self.assertEqual(FakeBrowser.made[1].given, [(None, None)])
        self.assertTrue(FakeBrowser.made[0].closed)
        self.assertEqual(self.client.get(base).status_code, 410)
        self.assertEqual(self.client.post(f"/api/accommodations/aim/{second}/close", json={}, headers=HEADERS).json, {"closed": True})
        self.assertEqual(self.client.get(f"/api/accommodations/aim/{second}").status_code, 410)
        self.assertEqual(self.client.get("/api/accommodations/aim/not-a-session").status_code, 410)

    def test_a_saved_sign_in_starts_without_the_form_and_never_leaves_the_server(self):
        folder = tempfile.mkdtemp(); self.addCleanup(lambda: __import__("shutil").rmtree(folder))
        path = os.path.join(folder, "gradescope.json")
        with open(path, "w") as handle:
            json.dump({"email": "jdoe@tamu.edu", "password": "hunter2"}, handle)
        os.chmod(path, 0o600)
        with patch.dict(os.environ, {aim_import.SAVED_ENV: path}):
            saved = self.client.get("/api/accommodations/aim/saved")
            self.assertEqual(saved.json, {"saved": True, "netid": "jdoe@tamu.edu", "problem": ""})
            started = self.client.post("/api/accommodations/aim", json={"saved": True}, headers=HEADERS)
            self.assertEqual(started.status_code, 201)
            self.assertNotIn("hunter2", saved.get_data(as_text=True) + started.get_data(as_text=True))
            self.assertEqual(FakeBrowser.made[-1].given, [("jdoe@tamu.edu", "hunter2")])
            os.chmod(path, 0o644)
            broken = self.client.get("/api/accommodations/aim/saved").json
            self.assertEqual((broken["saved"], "0600" in broken["problem"]), (False, True))
            self.assertEqual(self.client.post("/api/accommodations/aim", json={"saved": True}, headers=HEADERS).status_code, 400)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(aim_import.SAVED_ENV, None)
            self.assertEqual(self.client.get("/api/accommodations/aim/saved").json, {"saved": False, "netid": "", "problem": ""})
            self.assertEqual(self.client.post("/api/accommodations/aim", json={"saved": True}, headers=HEADERS).status_code, 400)

    def test_writes_keep_the_workspace_guard(self):
        self.assertEqual(self.client.post("/api/accommodations/aim", json={}).status_code, 403)
        self.assertEqual(FakeBrowser.made, [])


if __name__ == "__main__":
    unittest.main()
