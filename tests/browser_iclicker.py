"""Import from iClicker end to end in a real browser (AGENTS.md "iClicker"; gradebook/README.md "Importing from iClicker"): Section
507's gradebook offers Import from iClicker; the first time the dialog asks for the section's iClicker course with Faculty's proposal
preselected, then previews one column per ended polling session (only polls: an exit poll and a running session left out), a student matched by
the roster email, one Faculty cannot match, one category for every new column at once, the "did not join" tick, and imports. The
next import goes straight to the preview, Change course… shows the courses again, and Section 508's session of the same title widens
the column. Then Start from Canvas (the term's first Sync with Canvas, its own section's course ticked alone) creates each session's
Canvas assignment in the Checks for Understanding group and sends the scores (requirement, 2026-09-28 and 2026-10-06). Excusing Bob's absence from his gradebook record returns to
the record with the session's full credit (ICLICKER-CREDIT). The fake iClicker only ever answers reads. No console error,
page error or CSP violation. Synthetic data only.

Run from the project root: python tests/browser_iclicker.py (manage_checks.py browser --browser iclicker)."""
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app  # noqa: E402
from browser_course_pages import seed_course  # noqa: E402
from canvas import web as canvas_web  # noqa: E402
from fake_canvas import FakeCanvas  # noqa: E402
from fake_iclicker import EMAIL, PASSWORD, FakeIClicker  # noqa: E402
from iclicker import web  # noqa: E402
from rosters import connect  # noqa: E402
from test_rosters import STUDENTS, csv_bytes  # noqa: E402
from test_storage import HEADERS  # noqa: E402

ALICE, CAROL = "001234567", "222333444"


def main():
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeIClicker().start()
        course_a = fake.add_course("Math-140-507-Fall-26")
        course_b = fake.add_course("Math-140-508-Fall26")
        fake.add_course("26 SPRING MATH 142 510: BUSINESS CALCULUS-Sinkule", term=None, archived=True)
        alice = fake.add_student(course_a, "Alice", "Example", ALICE, "alice@example.test")
        bob = fake.add_student(course_a, "Bob", "Example", "bobby", "bob@example.test")
        fake.add_student(course_a, "Dana", "Newcomer", "", "dana@example.test")
        carol = fake.add_student(course_b, "Carol", "Example", CAROL, "carol@example.test")
        s1 = fake.add_session(course_a, "Session 1", "2026-09-01T15:00:00.000Z")
        s2 = fake.add_session(course_a, "Session 2", "2026-09-03T15:00:00.000Z")
        fake.add_session(course_a, "Exit poll", "2026-09-03T15:40:00.000Z", kind="EXIT_POLL")
        fake.add_session(course_a, "Session 3", "2026-09-05T15:00:00.000Z", ended=False)
        fake.score(course_a, s1, alice, 1.0, 1.0)
        fake.score(course_a, s1, bob, 1.0, 0.0)
        fake.score(course_a, s2, alice, 1.0, 1.0)
        b1 = fake.add_session(course_b, "Session 1", "2026-09-02T15:00:00.000Z")
        fake.score(course_b, b1, carol, 1.0, 1.0)
        sign_in = os.path.join(directory, "config", "sign-in.json")
        os.makedirs(os.path.dirname(sign_in), mode=0o700)
        with open(sign_in, "w", encoding="utf-8") as handle:
            json.dump({"email": EMAIL, "password": PASSWORD}, handle)
        os.chmod(sign_in, 0o600)
        web.forget_tokens()
        canvas = FakeCanvas().start()
        canvas_a, canvas_b = canvas.add_course("MATH-140-507", "MATH-140-507", "Fall 2026"), canvas.add_course("MATH-140-508", "MATH-140-508", "Fall 2026")
        for course in (canvas_a, canvas_b):
            canvas.groups[course] = [{"id": canvas.next_id(), "name": "Checks for Understanding", "position": 1, "assignments": []}]
        canvas_section = canvas.add_section(canvas_a, "507")
        canvas_alice = canvas.add_student(canvas_a, canvas_section, "Example, Alice", ALICE)
        canvas_bob = canvas.add_student(canvas_a, canvas_section, "Example, Bob", STUDENTS[1][2])

        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        app.extensions["iclicker"].settings = web.Settings.testing(sign_in, fake.url)
        canvas_settings = canvas_web.Settings(session_file=os.path.join(directory, "config", "canvas-session.json"), base=canvas.url)
        app.extensions["canvas"].settings = canvas_settings
        canvas_web.save(canvas_settings, canvas.session(), user={"name": "Pat Instructor"})
        seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]
        rows = [*STUDENTS, ["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@example.test"]]
        for number, chosen in ((507, rows[:2]), (508, rows[2:])):
            answer = client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster",
                                 data={"file": (io.BytesIO(csv_bytes(chosen)), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        term = client.get("/api/gradebook").json["terms"][0]["id"]
        saved = client.post(f"/api/gradebook/terms/{term}/setup", json={"base_revision": None, "request_id": "setup", "setup": {
            "categories": [{"ref": "cfu", "name": "Checks for Understanding"}], "items": [{"title": "Exam 1", "points": "100"}]}}, headers=HEADERS)
        assert saved.status_code == 200, saved.json
        category = saved.json["setup"]["categories"][0]["id"]
        with connect(app.config["DATABASE"]) as db:
            sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}
        for number, course in ((507, canvas_a), (508, canvas_b)):
            assert client.post(f"/api/canvas/sections/{sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS).status_code == 200

        server = create_server(app, host="127.0.0.1", port=0, threads=8)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(20000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                page.add_init_script("""window.cspViolations = [];
                  document.addEventListener('securitypolicyviolation', event => window.cspViolations.push([event.violatedDirective, event.blockedURI]));""")
                dialog = page.locator("#dialog")
                heading = page.locator(".page-heading")

                def open_import():
                    heading.locator(".page-heading-secondary").get_by_role("button", name="Import from iClicker", exact=True).click()
                    expect(dialog.get_by_role("heading", name="Import from iClicker")).to_be_visible()

                # ---- Section 507: the course is chosen first, with Faculty's proposal preselected --------------------------------
                page.goto(f"{address}/#course/{group['id']}/section/507")
                expect(page.locator('[data-gradebook-page="grid"]')).to_be_visible()
                open_import()
                picker = dialog.get_by_label("iClicker course")
                expect(picker).to_have_value(course_a)
                expect(dialog).to_contain_text("Proposed from the course and section numbers in its name.")
                expect(picker.locator("option")).to_have_count(4)  # "Choose…", the proposal, 508's, the archived one (no events)
                dialog.get_by_role("button", name="Use this course").click()
                # ---- The preview: one column per ended session -----------------------------------------------------------------
                imports = dialog.locator('[data-keep="group-import"]')  # grouped: each session shows where it goes, with Change
                expect(imports.locator("tr", has_text="Session 1")).to_contain_text("New column")
                expect(imports.locator("tr", has_text="Session 2")).to_contain_text("New column")
                expect(dialog.locator(".gbd-summary")).to_contain_text("Creates 2 new columns")
                expect(dialog.get_by_label("Where Exit poll goes")).to_have_count(0)
                expect(dialog.get_by_label("Where Session 3 goes")).to_have_count(0)
                expect(dialog).to_contain_text("Reads Math-140-507-Fall-26 (Section 507)")
                expect(dialog).to_contain_text("1 session not imported (only polls are)")
                expect(dialog).to_contain_text("1 iClicker session is still running and not imported yet.")
                expect(dialog).to_contain_text("1 iClicker student was matched by the email on the roster, not a UIN.")
                unmatched = dialog.locator('[data-keep="iClicker students Faculty could not match (no UIN or roster email)"]')
                expect(unmatched).to_contain_text("1")
                unmatched.locator("summary").click()
                expect(unmatched).to_contain_text("Newcomer, Dana")
                # One category for every new column at once.
                dialog.get_by_label("Put the 2 new columns in").select_option(category)
                expect(imports.locator("tr", has_text="Session 1")).to_contain_text("New column in")
                expect(imports.locator("tr", has_text="Session 2")).to_contain_text("New column in")
                dialog.get_by_label("Record a session a student did not join as missing (counts as zero)").check()
                submit = dialog.get_by_role("button", name="Import 4 scores · 2 new columns")
                expect(submit).to_be_enabled()
                text = dialog.inner_text()
                assert ALICE not in text and "example.test" not in text, "a UIN or an email reached the page"
                submit.click()
                expect(dialog).to_be_hidden()
                expect(page.locator("#toast")).to_contain_text("Imported 4 scores from iClicker · 2 new columns")
                expect(page.locator('[data-gradebook-page="grid"]')).to_contain_text("Session 1")
                view = client.get(f"/api/gradebook/terms/{term}?section={sections[507]}").json
                items = {item["title"]: item for item in view["setup"]["items"]}
                assert items["Session 1"]["category_id"] == category and items["Session 1"]["sections"] == [sections[507]], items["Session 1"]
                bob_row = next(s["row"] for s in view["students"] if s["name"] == "Example, Bob")
                assert view["cells"][items["Session 2"]["id"]][bob_row]["status"] == "missing"
                # ---- The next import goes straight to the preview; Change course… shows the courses again ----------------------
                open_import()
                expect(dialog.locator('[data-keep="group-import"] tr', has_text="Session 1")).to_contain_text("Session 1")
                expect(dialog).to_contain_text("Nothing to import.")
                dialog.get_by_role("button", name="Change course…").click()
                expect(dialog.get_by_label("iClicker course")).to_have_value(course_a)
                dialog.get_by_role("button", name="Keep Math-140-507-Fall-26").click()
                expect(dialog.locator('[data-keep="group-import"] tr', has_text="Session 1")).to_be_visible()
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()
                # ---- Section 508: the session of the same title widens the column ----------------------------------------------
                page.goto(f"{address}/#course/{group['id']}/section/508")
                expect(page.locator('[data-gradebook-page="grid"]')).to_be_visible()
                open_import()
                expect(dialog.get_by_label("iClicker course")).to_have_value(course_b)
                dialog.get_by_role("button", name="Use this course").click()
                row = dialog.locator('[data-keep="group-import"] tr', has_text="Session 1")
                row.get_by_role("button", name="Change").click()
                expect(dialog.get_by_label("Where Session 1 goes")).to_have_value(f"item:{items['Session 1']['id']}")
                expect(dialog).to_contain_text("The column will also apply to Section 508.")
                dialog.get_by_role("button", name="Import 1 score").click()
                expect(dialog).to_be_hidden()
                view = client.get(f"/api/gradebook/terms/{term}").json
                session_1 = next(item for item in view["setup"]["items"] if item["title"] == "Session 1")
                assert session_1["sections"] == sorted([sections[507], sections[508]]), session_1
                assert {method for method, path in fake.requests if path != "/authproxy/login"} == {"GET"}, fake.requests
                assert fake.logins == 1, fake.logins  # the token was kept in memory for every read
                # ---- Start from Canvas: each session's assignment is made in Checks for Understanding first ------------------------
                page.goto(f"{address}/#course/{group['id']}/section/507")
                expect(page.locator('[data-gradebook-page="grid"]')).to_be_visible()
                heading.locator(".page-heading-secondary").get_by_role("button", name="Start from Canvas", exact=True).click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible()
                summary = dialog.locator(".gbd-summary")
                expect(summary).to_contain_text("Sends 4 grades to Canvas", timeout=30000)
                expect(summary).to_contain_text("Creates 2 assignments in Canvas first")
                # Faculty has never written a grade to Canvas: this section's course alone is ticked (one real section first).
                expect(dialog.locator(f'[data-sync-course="{canvas_a}"]')).to_be_checked()
                expect(dialog.locator(f'[data-sync-course="{canvas_b}"]')).not_to_be_checked()
                expect(dialog.locator(".callout", has_text="Students see these grades")).to_be_visible()
                dialog.get_by_role("button", name="Sync · 4 grades to Canvas").click()
                expect(dialog).to_contain_text("Sent. 2 assignments created in Canvas; 4 grades now show in Canvas", timeout=30000)
                made = {a["name"]: a for g in canvas.groups[canvas_a] for a in g["assignments"]}
                assert set(made) == {"Session 1", "Session 2"} and all(a["points_possible"] == 10.0 and a["published"] for a in made.values()), made
                assert canvas.submissions[canvas_a][(canvas_alice, made["Session 1"]["id"])]["score"] == 2.0
                assert canvas.submissions[canvas_a][(canvas_bob, made["Session 2"]["id"])]["score"] == 0.0  # missing sends 0
                assert not canvas.groups[canvas_b][0]["assignments"], "a section tab's send made something in another section's course"
                # ---- Excused from Bob's record: the record comes back with the iClicker credit the save gave (ICLICKER-CREDIT) -----
                recorded = client.post("/api/absences", json={"section_id": sections[507], "enrollment_id": bob_row.split(":")[1], "first_date": "2026-09-03",
                                       "last_date": "2026-09-03", "reason": "illness", "status": "pending", "request_id": "bob-sep-3"}, headers=HEADERS)
                assert recorded.status_code == 201, recorded.json
                page.reload()  # the send's window closes with it
                expect(page.locator('[data-gradebook-page="grid"]')).to_be_visible()
                page.locator("[data-student]").filter(has_text="Example, Bob").first.click()
                record = page.locator("#gradebook-student-form")
                # The record opens folded; its sessions are under their category, which stays open when the record comes back.
                record.get_by_role("button", name="Checks for Understanding", exact=True).click()
                session_2 = record.get_by_role("region", name="Grades").locator("tr", has_text="Session 2")
                expect(session_2).to_contain_text("Missing")
                record.locator("[data-absences-card] [data-card-absence]").first.click()
                absence = page.locator("#absence-form")
                absence.get_by_label("Excused", exact=True).check()
                absence.get_by_role("button", name="Save changes").click()
                expect(page.locator("#toast")).to_contain_text("full credit for 1 iClicker session")
                expect(record).to_be_visible()
                expect(session_2).to_contain_text("10 of 10")
                expect(record.locator("[data-change]").first).to_be_visible()
                record.get_by_role("button", name="Close", exact=True).click()
                assert not page.evaluate("window.cspViolations"), page.evaluate("window.cspViolations")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
            canvas.stop()
            web.forget_tokens()
    print("iClicker browser check passed")


if __name__ == "__main__":
    main()
