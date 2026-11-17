"""Import from Howdy end to end in real browsers (CANVAS "IMPORT FROM HOWDY"). Import rosters' Import from Howdy starts the
server's private browser, here an ordinary Chromium on its own virtual display, with the sign-in saved on the server: past a stand-in
of Cloudflare's browser check, TAMU's Microsoft-style NetID and password pages and a Duo push, to a stand-in Howdy whose My Courses
page asks Howdy's API with a Bearer token, as Howdy's does. Faculty reads the course's sections there, and Import rosters imports
each section's roster at once: a Q drop on the registrar's roster is dropped, a section Howdy does not list is named. Everything
stand-in lives in this file and is synthetic."""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, make_response, redirect, request
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aim_import
from app import create_app
from browser_course_pages import seed_course

EMAIL, PASSWORD = "instructor@tamu.edu", "correct horse battery"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJpbnN0cnVjdG9yIn0.c2lnbmF0dXJlLW9mLXRoZS1zdGFuZC1pbg"
DUO = "<p>Secured by Duo</p>"
COURSES = [{"CRN": "33168", "COURSE": "MATH-140-507", "COURSE_TERM_CODE": "202631"},
           {"CRN": "33192", "COURSE": "MATH-140-508", "COURSE_TERM_CODE": "202631"},
           {"CRN": "40001", "COURSE": "MATH-142-501", "COURSE_TERM_CODE": "202631"}]
ROSTERS = {
    "33168": [{"FULL_NAME": "Aggie, Reveille", "UIN": "012345678", "STVRSTS_DESC": "Web Registered", "MAJOR": "Economics",
               "CLASSIFICATION": "U1", "EMAIL_ADDRESS": "rev@email.tamu.edu"},
              {"FULL_NAME": "Smith, Sam", "UIN": "223456789", "STVRSTS_DESC": "Registered", "MAJOR": "Finance",
               "CLASSIFICATION": "U2", "EMAIL_ADDRESS": "sam@email.tamu.edu"}],
    "33192": [{"FULL_NAME": "Doe, Jordan", "UIN": "323456789", "STVRSTS_DESC": "Q-Drop State Limited", "SFRSTCR_GRDE_CODE": "Q",
               "EMAIL_ADDRESS": "jd@email.tamu.edu"},
              {"FULL_NAME": "Lee, Avery", "UIN": "423456789", "STVRSTS_DESC": "Web Registered", "EMAIL_ADDRESS": "al@email.tamu.edu"}],
}


def page(body, script=""):
    return f"<!doctype html><html><head><style>body{{margin:0;font:16px sans-serif}}</style></head><body>{body}{script}</body></html>"


def stand_in(seen):
    """Cloudflare's check, TAMU's sign-in, Duo and Howdy as far as Faculty meets them. Howdy answers at 127.0.0.1 and the sign-in
    at localhost, so they are two sites, as Howdy and Microsoft are."""
    howdy = Flask("stand_in_howdy")

    def at(host, path):
        return f"http://{host}:{request.host.rsplit(':', 1)[1]}{path}"

    def signed():
        return request.cookies.get("session") == "signed-in"

    @howdy.get("/uPortal/azureSignIn")
    def sign_in():
        if request.cookies.get("cf") != "passed":  # Cloudflare's check: it passes by itself in an ordinary browser
            seen["checks"] += 1
            return page("<h1>Performing security verification</h1><p>This website uses a security service to protect against malicious bots.</p>",
                        '<script>setTimeout(() => { document.cookie = "cf=passed; path=/"; location.reload(); }, 800);</script>')
        return redirect(at("localhost", "/ms/login"))

    @howdy.route("/ms/login", methods=["GET", "POST"])
    def email():
        if request.method == "POST":
            seen["netids"].append(request.form.get("loginfmt"))
            return redirect("/ms/password")
        return page('<h1>Sign in</h1><form method="post"><input type="email" name="loginfmt" autofocus><button>Next</button></form>')

    @howdy.route("/ms/password", methods=["GET", "POST"])
    def password():
        if request.method == "POST":
            seen["passwords"] += 1
            if request.form.get("passwd") == PASSWORD:
                return redirect("/duo")
        return page('<h1>Enter password</h1><form method="post"><input type="password" name="passwd" autofocus><button>Sign in</button></form>')

    @howdy.get("/duo")
    def duo():
        return page(f'{DUO}<h1>Check for a Duo Push</h1><p>Verify it\'s you by approving the notification.</p>',
                    '<script>setInterval(async () => { if ((await (await fetch("/duo/state")).text()) === "approved") location = "/duo/done"; }, 300);</script>')

    @howdy.get("/duo/state")
    def duo_state():
        return "approved" if seen["approved"] else "waiting"

    @howdy.get("/duo/done")
    def duo_done():
        return redirect(at("127.0.0.1", "/uPortal/Login?ticket=stand-in"))

    @howdy.get("/uPortal/Login")
    def portal_login():
        answer = redirect("/uPortal/f/howdy-dashboard/normal/render.uP")
        answer.set_cookie("session", "signed-in")
        return answer

    @howdy.get("/uPortal/normal/render.uP")
    def guest():
        return page('<p>It looks like the page you were attempting to go to does not exist so we redirected you here.</p><a href="/uPortal/azureSignIn">Sign In</a>')

    @howdy.get("/uPortal/f/howdy-dashboard/normal/render.uP")
    def dashboard():
        if not signed():
            return redirect("/uPortal/normal/render.uP")
        # Howdy's dashboard: My Courses asks Howdy's API with the token uPortal gives the page.
        return page('<nav>Instructor/Associate · My Courses</nav><a href="#">Sign Out</a><main id="courses">Select term</main>',
                    """<script>
                    async function courses() {
                      if (location.hash !== "#MyCourses") return;
                      const token = await (await fetch("/uPortal/api/v5-1/userinfo")).text();
                      const answer = await fetch("/api/get-instructor-courses", {method: "POST", headers: {"Authorization": "Bearer " + token,
                        "Content-Type": "application/json"}, body: JSON.stringify({termCode: "202631"})});
                      document.querySelector("#courses").textContent = (await answer.json()).length + " courses";
                    }
                    addEventListener("hashchange", courses); courses();
                    </script>""")

    @howdy.get("/uPortal/api/v5-1/userinfo")
    def userinfo():
        if not signed():
            return "", 401
        return make_response(TOKEN, 200, {"Content-Type": "application/jwt"})

    def bearer():
        seen["api"].append((request.path, request.headers.get("Authorization") == f"Bearer {TOKEN}", request.get_json(silent=True)))
        return signed() and request.headers.get("Authorization") == f"Bearer {TOKEN}"

    @howdy.post("/api/get-instructor-courses")
    def courses():
        if not bearer():
            return jsonify(error="unauthorized"), 401
        return jsonify(COURSES if request.get_json().get("termCode") == "202631" else [])

    @howdy.post("/api/class-rosters")
    def rosters():
        if not bearer():
            return jsonify(error="unauthorized"), 401
        return jsonify(ROSTERS.get(request.get_json().get("crn"), []))

    return howdy


def main():
    with tempfile.TemporaryDirectory() as directory:
        seen = {"checks": 0, "netids": [], "passwords": 0, "approved": False, "api": []}
        howdy_server = create_server(stand_in(seen), host="127.0.0.1", port=0)
        threading.Thread(target=howdy_server.run, daemon=True).start()
        app = create_app(directory); client = app.test_client(); client.get("/").close()
        app.config["HOWDY_PORTAL"] = f"http://127.0.0.1:{howdy_server.effective_port}/uPortal/azureSignIn"
        sessions = app.extensions["howdy_sessions"]
        seed_course(client, code="MATH 140", sections=[507, 508, 514])
        group = client.get("/api/state").json["course_groups"][0]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        saved = Path(directory) / "gradescope.json"
        saved.write_text(json.dumps({"email": EMAIL, "password": PASSWORD})); os.chmod(saved, 0o600)
        os.environ[aim_import.SAVED_ENV] = str(saved)

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
                page_.goto(address + "/#all-courses")
                page_.get_by_role("link", name="Open MATH 140 · Fall 2026", exact=True).click()
                page_.get_by_role("button", name="Import rosters", exact=True).click()
                dialog = page_.get_by_role("dialog")
                expect(dialog).to_contain_text("Students the registrar marks as a Q drop or withdrawal are dropped")
                Path("test-results").mkdir(exist_ok=True)
                dialog.screenshot(path="test-results/howdy-import-rosters.png")
                dialog.get_by_role("button", name="Import from Howdy", exact=True).click()

                # 1. The saved sign-in goes straight on: past Cloudflare's check, the NetID and password, to Duo's push.
                expect(dialog.locator("[data-aim-who]")).to_have_text(f"Signing in as {EMAIL} with the sign-in saved on the server.")
                step = dialog.locator("[data-aim-step]")
                expect(step).to_have_text("Approve the Duo push on your phone.", timeout=60000)
                assert seen["checks"] >= 1 and seen["netids"] == [EMAIL] and seen["passwords"] == 1, seen
                assert sessions._current.HEADLESS is False, "Howdy's browser is an ordinary one, on its own display"
                seen["approved"] = True

                # 2. Signed in: Faculty reads each section's roster with Howdy's own token, and the rosters import at once.
                multi = page_.locator("#roster-multi-form")
                expect(multi).to_be_visible(timeout=60000)
                results = multi.locator("[data-roster-result]")
                expect(results).to_have_count(2)
                expect(results.nth(0)).to_have_text("Section 507: 2 students · 2 added · 0 updated.", timeout=20000)
                expect(results.nth(1)).to_contain_text("Section 508: 1 students · 2 added · 0 updated.")  # the Q drop is dropped at once
                expect(results.nth(1)).to_contain_text("Dropped: Doe, Jordan (Q drop).")
                expect(multi.locator("[data-roster-howdy-problems]")).to_have_text(
                    "Howdy lists no MATH 140 514 among your Fall 2026 courses.")
                expect(multi.get_by_label("Roster exports", exact=True)).to_be_hidden()
                multi.screenshot(path="test-results/howdy-imported.png")
                assert all(authorized for _path, authorized, _body in seen["api"]), seen["api"]
                rosters = [body for path, _ok, body in seen["api"] if path == "/api/class-rosters"]
                assert rosters == [{"crn": "33168", "termCode": "202631"}, {"crn": "33192", "termCode": "202631"}], rosters
                assert gone(), "the server's browser, its token and the rosters are discarded once the page has the files"
                multi.get_by_role("button", name="Close", exact=True).click()

                base = f"/api/course-groups/{group['id']}/sections"
                first = client.get(f"{base}/507/roster").json
                assert first["count"] == 2 and first["last_import"]["filename"].startswith("MATH-140-507_202631-"), first["last_import"]
                uins = sorted(student["uin"] for student in first["students"])
                assert uins == ["012345678", "223456789"], uins
                second = client.get(f"{base}/508/roster").json
                assert [row["name"] for row in second["last_import"]["registrar_dropped"]] == ["Doe, Jordan"], second["last_import"]
                assert client.get(f"{base}/514/roster").json["count"] == 0
                expect(page_.locator("#section-tile-507")).to_have_text("2 students")
                assert not errors, errors
                browser.close()
        finally:
            os.environ.pop(aim_import.SAVED_ENV, None)
            server.close(); howdy_server.close()
    print("PASS Import from Howdy")


if __name__ == "__main__":
    main()
