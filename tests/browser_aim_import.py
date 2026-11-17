"""Import from AIM end to end in real browsers. Faculty's form takes a NetID and password; the private server-side browser
signs in to a stand-in of TAMU's sign-in (Microsoft-style NetID and password pages, a Duo verified push with a timed-out
push, other options and a passcode, "Is this your device?", "Stay signed in?"), continues past the stand-in AIM's
access-policy page by itself and exports; both exports open in one review. A page the driver does not recognize opens
the live page, where the test clicks through. Everything stand-in lives in this file and is synthetic."""
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, make_response, redirect, request
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aim_import
from aim_import import PREFIX
from app import create_app
from browser_course_pages import seed_course
from test_rosters import csv_bytes
from test_accommodations import workbook as accommodation_workbook
from test_dr_exams import workbook as exam_workbook
from test_storage import HEADERS

NETID, EMAIL, PASSWORD, PASSCODE = "instructor", "instructor@tamu.edu", "correct horse battery", "123456"
STATE = "dDwtMTIzNDU2Nzg5Ozs+"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORT = {"Overview.aspx": PREFIX + "PnlExport$BtnExport", "Testing.aspx": PREFIX + "GFormUpload$BtnExportData"}
DUO = "<p>Secured by Duo</p>"


def page(body, script=""):
    return f"<!doctype html><html><head><style>body{{margin:0;font:16px sans-serif}}</style></head><body>{body}{script}</body></html>"


def stand_in(files, seen):
    """TAMU's sign-in, Duo and AIM as far as Faculty meets them."""
    aim = Flask("stand_in_aim")

    @aim.route("/ms/login", methods=["GET", "POST"])
    def email():
        complaint = ""
        if request.method == "POST":
            seen["netids"].append(request.form.get("loginfmt"))
            if request.form.get("loginfmt") == EMAIL:
                return redirect("/ms/password")
            complaint = "<p>We couldn't find an account with that username.</p>"
        return page(f'<h1>Sign in</h1>{complaint}<form method="post"><input type="email" name="loginfmt" placeholder="NetID@tamu.edu" autofocus><button>Next</button></form>')

    @aim.route("/ms/password", methods=["GET", "POST"])
    def password():
        complaint = ""
        if request.method == "POST":
            seen["passwords"] += 1
            if request.form.get("passwd") == PASSWORD:
                return redirect("/ms/verify")
            complaint = "<p>Your account or password is incorrect. If you don't remember your password, reset it now.</p>"
        return page(f'<h1>Enter password</h1>{complaint}<form method="post"><input type="password" name="passwd" autofocus><button>Sign in</button></form>')

    @aim.route("/ms/verify", methods=["GET", "POST"])
    def verify():
        # TAMU's page before Duo. Like Microsoft's, it keeps the earlier password field in the page, off screen and faded.
        if request.method == "POST":
            seen["verified"] += 1
            return redirect("/ms/interstitial" if seen["interstitial"] else "/duo")
        return page('<p>instructor@tamu.edu</p><h1>Verification Required</h1><p>You will be redirected to Duo to verify your identity</p>'
                    '<form method="post"><button>Continue</button></form>'
                    '<div style="position:absolute;left:-9999px"><input type="password" name="passwd"></div>'
                    '<div style="opacity:0"><input type="password" name="passwd2"></div>')

    @aim.route("/ms/interstitial", methods=["GET", "POST"])
    def interstitial():
        if request.method == "POST":
            seen["interstitial"] = False
            return redirect("/duo")
        # A step the driver cannot recognize; its button sits where the test clicks the picture.
        return page('<h1>More information required</h1><p>Your organization needs more information to keep your account secure.</p>'
                    '<form method="post"><button style="position:absolute;left:400px;top:300px;width:300px;height:60px">Ask later</button></form>')

    @aim.get("/duo")
    def duo():
        if seen["duo"] == "timeout":
            return page(f'{DUO}<h1>Push timed out</h1><form method="post" action="/duo/retry"><button>Try again</button></form><a href="/duo/other">Other options</a>')
        return page(f'{DUO}<h1>Enter code in Duo Mobile</h1><div>{seen["code"]}</div><p>Verify it\'s you by entering this verification code.</p><a href="/duo/other">Other options</a>',
                    '<script>setInterval(async () => { if ((await (await fetch("/duo/state")).text()) === "approved") location = "/duo/trust"; }, 300);</script>')

    @aim.get("/duo/state")
    def duo_state():
        return "approved" if seen["approved"] else "waiting"

    @aim.post("/duo/retry")
    def duo_retry():
        seen["duo"], seen["code"], seen["pushes"] = "code", "915", seen["pushes"] + 1
        return redirect("/duo")

    @aim.get("/duo/other")
    def duo_other():
        return page(f'{DUO}<h1>Other options to log in</h1><a href="/duo/push">Duo Push</a><br><a href="/duo/passcode">Duo Mobile passcode</a>')

    @aim.get("/duo/push")
    def duo_push():
        seen["duo"], seen["pushes"] = "code", seen["pushes"] + 1
        return redirect("/duo")

    @aim.route("/duo/passcode", methods=["GET", "POST"])
    def duo_passcode():
        complaint = ""
        if request.method == "POST":
            if request.form.get("passcode-input") == PASSCODE:
                return redirect("/duo/trust")
            complaint = "<p>Incorrect passcode. Enter a new one.</p>"
        return page(f'{DUO}<h1>Enter a passcode</h1>{complaint}<form method="post"><input name="passcode-input" id="passcode-input"><button>Verify</button></form>')

    @aim.route("/duo/trust", methods=["GET", "POST"])
    def duo_trust():
        if request.method == "POST":
            seen["trusted"] = request.form.get("trust")
            return redirect("/ms/kmsi")
        return page(f'{DUO}<h1>Is this your device?</h1><form method="post"><button name="trust" value="yes">Yes, this is my device</button>'
                    '<button name="trust" value="no">No, other people use this device</button></form>')

    @aim.route("/ms/kmsi", methods=["GET", "POST"])
    def kmsi():
        if request.method == "POST":
            seen["stayed"] = request.form.get("stay")
            response = redirect("/tamu/instructor/Login.aspx"); response.set_cookie("signed", "1")
            return response
        return page('<h1>Stay signed in?</h1><p>Do this to reduce the number of times you are asked to sign in.</p>'
                    '<form method="post"><button name="stay" value="no">No</button><button name="stay" value="yes">Yes</button></form>')

    def guard():
        if request.cookies.get("signed") != "1":
            return redirect("/ms/login")
        if request.cookies.get("policy") != "1":
            return redirect("/tamu/instructor/Login.aspx")
        return None

    @aim.route("/tamu/instructor/Login.aspx", methods=["GET", "POST"])
    def policy():
        if request.cookies.get("signed") != "1":
            return redirect("/ms/login")
        if request.method == "POST":
            seen["policy"] += 1
            response = redirect("/tamu/instructor/Overview.aspx"); response.set_cookie("policy", "1")
            return response
        return page('<h1>ACCESS POLICY</h1><p>By clicking on the following button, I acknowledge the FERPA policy.</p>'
                    '<form method="post"><button>CONTINUE TO VIEW STUDENT ACCOMMODATIONS</button></form>')

    @aim.get("/tamu/instructor")
    def portal():
        return guard() or redirect("/tamu/instructor/Overview.aspx")

    @aim.route("/tamu/instructor/<name>", methods=["GET", "POST"])
    def aim_page(name):
        if name not in EXPORT:
            return "", 404
        refused = guard()
        if refused:
            return refused
        target = EXPORT[name]
        if request.method == "POST":
            seen["posts"].append((name, request.args.to_dict(), request.form.to_dict(flat=False)))
            form = request.form
            if form.get("__VIEWSTATE") == STATE and form.get("__EVENTTARGET") == target and form.get("__EVENTARGUMENT") == "":
                filename, body = files[name]
                response = make_response(body); response.headers["Content-Type"] = XLSX
                response.headers["Content-Disposition"] = f"attachment; filename={filename}"
                return response
            return page("Something went wrong")
        extra = (f'<p>Previous Term <span>Term: Fall 2026</span> Next Term</p><select name="{PREFIX}PnlExport$DDLCancelled$DDLInput">'
                 '<option value="0" selected>Do Not Include Cancelled Requests</option><option value="1">Include All Requests</option></select>'
                 if name == "Overview.aspx" else f'<input type="checkbox" name="{PREFIX}GV$ctl02$ChkBox"><input type="file" name="{PREFIX}GFormUpload$FileUpload">')
        action = "./Overview.aspx" if name == "Overview.aspx" else "./Testing.aspx?Step=Exams"
        return page(f'<h1>{name}</h1><form method="post" action="{action}"><input type="hidden" name="__EVENTTARGET" value="">'
                    f'<input type="hidden" name="__EVENTARGUMENT" value=""><input type="hidden" name="__VIEWSTATE" value="{STATE}">{extra}'
                    f"""<button type="button" name="{target}" onclick="__doPostBack('{target}','')">Export</button></form>""")

    return aim


def main():
    aim_import.STEP_WAIT = 2  # the live page opens sooner for a step Faculty does not recognize
    with tempfile.TemporaryDirectory() as directory:
        seen = {"netids": [], "passwords": 0, "verified": 0, "interstitial": False, "duo": "timeout", "code": "482", "approved": False, "pushes": 0,
                "trusted": None, "stayed": None, "policy": 0, "posts": []}
        files = {"Overview.aspx": ("Accommodation_Request_20260926.xlsx", accommodation_workbook()),
                 "Testing.aspx": ("Exams_20260926.xlsx", exam_workbook())}
        aim_server = create_server(stand_in(files, seen), host="127.0.0.1", port=0)
        threading.Thread(target=aim_server.run, daemon=True).start()
        app = create_app(directory); client = app.test_client(); client.get("/").close()
        app.config["AIM_PORTAL"] = f"http://127.0.0.1:{aim_server.effective_port}/tamu/instructor"
        sessions = app.extensions["aim_sessions"]
        seed_course(client, code="MATH 140", sections=[507], multiple_sections=False)
        group = client.get("/api/state").json["course_groups"][0]
        roster = f"/api/course-groups/{group['id']}/sections/507/roster"
        assert client.post(roster, data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS).status_code == 201
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"

        def gone(timeout=10):
            deadline = time.monotonic() + timeout
            while sessions._current is not None and time.monotonic() < deadline:
                time.sleep(0.1)
            return sessions._current is None

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page_ = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []; page_.on("pageerror", lambda error: errors.append(str(error)))
                page_.goto(address + "/#accommodations")
                actions = page_.locator("#accommodations-section .accommodation-actions")
                expect(actions.locator(".button").first).to_have_text("Import from AIM")
                dialog = page_.get_by_role("dialog")
                step = dialog.locator("[data-aim-step]")

                # 1. A wrong password comes back with TAMU's own words; the right one goes on to Duo.
                actions.get_by_role("button", name="Import from AIM", exact=True).click()
                expect(dialog).to_contain_text("FERPA access-policy page for you")
                dialog.get_by_label("NetID", exact=True).fill(NETID)
                dialog.get_by_label("Password", exact=True).fill("wrong password")
                dialog.get_by_role("button", name="Sign in and import", exact=True).click()
                expect(dialog.locator('[role="alert"]')).to_contain_text("Your account or password is incorrect", timeout=30000)
                assert seen["netids"] == [EMAIL], seen["netids"]
                expect(dialog.get_by_label("Password", exact=True)).to_be_visible()
                expect(dialog.get_by_label("Password", exact=True)).to_have_value("")
                dialog.get_by_label("Password", exact=True).fill(PASSWORD)
                dialog.get_by_role("button", name="Try again", exact=True).click()

                # 2. The push timed out: Send the push again, then the code shows here; approving it goes on by itself.
                expect(step).to_have_text("Duo did not get an answer.", timeout=20000)
                expect(dialog.locator('[role="alert"]')).to_contain_text("Push timed out")
                dialog.get_by_role("button", name="Send the push again", exact=True).click()
                expect(dialog.locator("[data-aim-digits]")).to_have_text("915", timeout=20000)
                expect(step).to_have_text("Open Duo Mobile and enter the code below.")
                assert seen["passwords"] == 2 and seen["pushes"] == 1 and seen["verified"] == 1, seen
                seen["approved"] = True

                # 3. Signed in, past the policy page and exported without a click: one review of both exports.
                review = dialog.locator("#aim-import-form")
                expect(review).to_be_visible(timeout=30000)
                assert (seen["trusted"], seen["stayed"], seen["policy"]) == ("no", "no", 1), seen
                expect(review.locator('[data-aim-preview="accommodations"]')).to_contain_text("MATH 140", timeout=15000)
                expect(review.locator('[data-aim-preview="exams"]')).to_contain_text("MATH 140", timeout=15000)
                expect(review).to_contain_text("Exported from AIM · Fall 2026")
                assert len(seen["posts"]) == 2, seen["posts"]
                for _name, _args, form in seen["posts"]:
                    assert not any(key.endswith(("FileUpload", "ChkBox")) for key in form), form
                assert seen["posts"][0][2][PREFIX + "PnlExport$DDLCancelled$DDLInput"] == ["0"]
                assert seen["posts"][1][1] == {"Step": "Exams"}
                assert gone(), "the server's browser, its password and the files are discarded once the page has the files"
                assert client.get(roster).json["accommodation_count"] == 0
                review.get_by_role("button", name="Import both", exact=True).click()
                expect(review.locator('[data-aim-status="accommodations"]')).to_contain_text("Import queued")
                expect(review.locator('[data-aim-status="exams"]')).to_contain_text("Import queued")
                assert app.extensions["inbox"].process_one()
                expect(review.locator('[data-aim-status="accommodations"]')).to_contain_text("Accommodations imported", timeout=10000)
                expect(review.locator('[data-aim-status="exams"]')).to_contain_text("Import queued")
                # Closed before the DR bookings job ends (2026-11-01), the box still learns that it did.
                dialog.get_by_role("button", name="Close", exact=True).click()
                assert app.extensions["inbox"].process_one()
                assert client.get(roster).json["accommodation_count"] == 1
                students = client.get("/api/accommodations/overview").json["students"]
                assert sum(len(student.get("dr_exams") or []) for student in students) == 1, students
                notice = page_.locator(".accommodation-aim")
                expect(notice).to_contain_text("Both exports are imported.")
                notice.get_by_role("button", name="Dismiss", exact=True).click()

                # 4. A step Faculty does not recognize opens the live page; clicking through it hands back to the driver,
                #    and a Duo passcode is used instead of the push. The NetID is remembered in this browser.
                seen.update(interstitial=True, duo="code", code="777", approved=False, posts=[], trusted=None, stayed=None)
                actions.get_by_role("button", name="Import from AIM", exact=True).click()
                expect(dialog.get_by_label("NetID", exact=True)).to_have_value(NETID)
                dialog.get_by_label("Password", exact=True).fill(PASSWORD)
                dialog.get_by_role("button", name="Sign in and import", exact=True).click()
                shown = dialog.locator("[data-aim-page]")
                expect(shown).to_have_attribute("open", "", timeout=30000)
                expect(dialog.locator("canvas[data-aim-frame]")).to_be_visible(timeout=15000)
                keys = dialog.locator("[data-aim-keys]")
                box = keys.bounding_box()
                page_.mouse.click(box["x"] + 550 / 1100 * box["width"], box["y"] + 330 / 760 * box["height"])
                expect(dialog.locator("[data-aim-digits]")).to_have_text("777", timeout=20000)
                dialog.get_by_text("Use a Duo passcode instead", exact=True).click()
                dialog.get_by_label("Passcode", exact=True).fill(PASSCODE)
                dialog.get_by_role("button", name="Use passcode", exact=True).click()
                expect(dialog.locator("#aim-import-form")).to_be_visible(timeout=30000)
                assert (seen["trusted"], seen["stayed"]) == ("no", "no"), seen
                assert len(seen["posts"]) == 2
                dialog.get_by_role("button", name="Close", exact=True).click()
                expect(notice).to_contain_text("Not imported yet")
                expect(notice.get_by_role("button", name="Review and import", exact=True)).to_be_visible()
                assert gone()

                # 5. Cancel before signing in finishes discards the server's browser.
                seen.update(duo="code", approved=False)
                notice.get_by_role("button", name="Dismiss", exact=True).click()
                actions.get_by_role("button", name="Import from AIM", exact=True).click()
                dialog.get_by_label("Password", exact=True).fill(PASSWORD)
                dialog.get_by_role("button", name="Sign in and import", exact=True).click()
                expect(dialog.locator("[data-aim-digits]")).to_have_text("777", timeout=30000)
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert gone()

                # 6. With a sign-in saved on the server (the Gradescope one) there is no form: straight to Duo's code.
                saved = Path(directory) / "gradescope.json"
                saved.write_text(json.dumps({"email": EMAIL, "password": PASSWORD})); os.chmod(saved, 0o600)
                os.environ[aim_import.SAVED_ENV] = str(saved)
                try:
                    signed = seen["passwords"]
                    actions.get_by_role("button", name="Import from AIM", exact=True).click()
                    expect(dialog.locator("[data-aim-who]")).to_have_text(f"Signing in as {EMAIL} with the sign-in saved on the server.")
                    expect(dialog.get_by_label("Password", exact=True)).to_be_hidden()
                    expect(dialog.locator("[data-aim-ferpa]")).to_be_visible()
                    expect(dialog.locator("[data-aim-digits]")).to_have_text("777", timeout=30000)
                    assert seen["netids"][-1] == EMAIL and seen["passwords"] == signed + 1, seen
                    dialog.get_by_role("button", name="Cancel", exact=True).click()
                    assert gone()
                finally:
                    os.environ.pop(aim_import.SAVED_ENV, None)
                assert not errors, errors
                browser.close()
        finally:
            server.close(); aim_server.close()
    print("PASS Import from AIM")


if __name__ == "__main__":
    main()
