"""Canvas end to end in a real browser (AGENTS.md "Canvas"): a section tab shows its Canvas line; Link a Canvas course needs a
sign-in, so the sign-in window opens, Faculty's private server-side browser signs in to the stand-in (TAMU's NetID and password
pages on another host, a Duo verified push, "Is this your device?", "Stay signed in?") and keeps Canvas's session; the link
dialog then lists the courses with the proposal, the link is saved, and Compare roster names who is where. Synthetic data only."""
import io
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from canvas import web
from fake_canvas import EMAIL, PASSWORD, FakeCanvas
from test_rosters import csv_bytes
from test_storage import HEADERS


def main():
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        course = fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026C", "Fall 2026")
        fake.add_course("MATH-140-508: Mathematics for Business", "MATH-140-508-2026C", "Fall 2026")
        section = fake.add_section(course, "MATH-140-507")
        fake.add_student(course, section, "Example, Alice", "001234567")
        fake.add_student(course, section, "Newcomer, Dana", "999888777")
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        session_file = os.path.join(directory, "config", "canvas-session.json")
        app.extensions["canvas"].settings = web.Settings(session_file=session_file, base=fake.url)
        seed_course(client, code="MATH 140", sections=[507], multiple_sections=False)
        group = client.get("/api/state").json["course_groups"][0]
        roster = f"/api/course-groups/{group['id']}/sections/507/roster"
        assert client.post(roster, data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS).status_code == 201
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Before any link, the course's Canvas content page names the step and links the section tab where it is done.
                page.goto(f"{address}/#course/{group['id']}/home/canvas")
                empty = page.locator("[data-canvas-content-host] .empty-state")
                expect(empty).to_contain_text("No Canvas courses linked yet", timeout=20000)
                expect(empty.get_by_role("link", name="Open Section 507")).to_have_attribute("href", re.compile(r"^#course/.+/section/507$"))
                page.goto(f"{address}/#course/{group['id']}/section/507?from=current-courses")
                row = page.locator("[data-canvas-row]")
                expect(row).to_be_visible(timeout=20000)
                expect(row).to_contain_text("No Canvas course linked")
                row.get_by_role("button", name="Link a Canvas course…").click()
                # Canvas wants a sign-in: the window opens on the NetID form (no saved sign-in here).
                dialog = page.locator("#dialog")
                expect(dialog.get_by_role("heading", name="Sign in to Canvas")).to_be_visible(timeout=20000)
                dialog.get_by_label("NetID").fill("instructor")
                dialog.get_by_label("Password").fill(PASSWORD)
                dialog.get_by_role("button", name="Sign in", exact=True).click()
                expect(dialog.locator("[data-aim-digits]")).to_have_text("482", timeout=60000)
                fake.seen["approved"] = True
                # Signed in: the session is kept, the browser is gone and the link dialog comes back with the courses.
                expect(dialog.get_by_role("heading", name="Canvas course")).to_be_visible(timeout=60000)
                select = dialog.get_by_label("Canvas course")
                expect(select).to_have_value(course, timeout=20000)
                expect(dialog.locator("[data-canvas-proposed]")).to_be_visible()
                expect(dialog.locator("[data-canvas-who]")).to_have_text("Signed in to Canvas as Pat Instructor.")
                assert fake.seen["netids"] == [EMAIL], fake.seen["netids"]
                assert (fake.seen["trusted"], fake.seen["stayed"]) == ("no", "no"), fake.seen
                assert os.stat(session_file).st_mode & 0o777 == 0o600
                assert PASSWORD not in Path(session_file).read_text()
                dialog.get_by_role("button", name="Save").click()
                expect(row.get_by_role("link", name="MATH-140-507: Mathematics for Business")).to_be_visible(timeout=20000)
                row.get_by_role("button", name="Compare roster…").click()
                expect(dialog).to_contain_text("1 of Faculty’s 2 current students are in Canvas; Canvas lists 2.", timeout=20000)
                expect(dialog).to_contain_text("Example, Bob")
                expect(dialog).to_contain_text("Newcomer, Dana")
                assert "001234567" not in dialog.inner_text()
                # Bob is not in Canvas: drop him from the section (asked once more before anything is written).
                dialog.get_by_label("Example, Bob").check()
                dialog.get_by_role("button", name="Drop selected from Section 507").click()
                expect(dialog.locator("[data-canvas-confirm]")).to_contain_text("Drop 1 student from Section 507?")
                dialog.locator("[data-canvas-confirm-drop]").click()
                expect(dialog).to_contain_text("Dropped 1 student from Section 507.", timeout=20000)
                expect(dialog).to_contain_text("1 of Faculty’s 1 current students are in Canvas; Canvas lists 2. Matched by UIN (Canvas’s SIS ID) exactly. 1 dropped here.")
                dialog.get_by_role("button", name="Close", exact=True).click()
                page.locator("[data-course-roster-link]").click()  # the roster is a link from the section's gradebook
                roster_panel = page.locator("#section-roster")
                expect(roster_panel.locator(".roster-summary")).to_contain_text("1 student · 1 dropped", timeout=20000)
                expect(roster_panel.get_by_role("button", name="Example, Bob")).to_be_hidden()
                roster_panel.get_by_role("button", name=re.compile("^Dropped")).click()
                roster_panel.get_by_role("button", name="Example, Bob").click()
                expect(dialog.get_by_role("button", name="Restore to the roster")).to_be_visible()
                dialog.get_by_role("button", name="Restore to the roster").click()
                expect(roster_panel.locator(".roster-summary")).to_contain_text("2 students", timeout=20000)
                # Start from Canvas (SYNC WITH CANVAS, the term's first sync): an unmatched graded assignment becomes a new column (in a
                # new category named after its Canvas assignment group), after a summary that writes nothing.
                quiz = fake.add_assignment(course, "Quiz 1", 10, group="Quizzes", post_manually=True)  # as the live courses post
                fake.grade(course, quiz, fake.enrollments[course][0]["user_id"], 9, posted=False)
                term = client.get("/api/gradebook").json["terms"][0]["id"]
                # A gradebook column Canvas has under another name ("Participation points"), with a grade typed for Alice that
                # Canvas disagrees with: listed as having no Canvas assignment, linked by search, its grades checked, and Alice's
                # grade held for a choice.
                saved = client.post(f"/api/gradebook/terms/{term}/setup", json={"base_revision": None, "request_id": "b-setup",
                                    "setup": {"items": [{"title": "Participation", "points": "2"}]}}, headers=HEADERS).json
                participation = saved["setup"]["items"][0]["id"]
                alice_row = next(st["row"] for st in saved["students"] if st["name"] == "Example, Alice")
                assert client.post(f"/api/gradebook/terms/{term}/scores", json={"request_id": "b-typed", "changes": [
                    {"item_id": participation, "row": alice_row, "base": None, "status": "scored", "points": "2"}]}, headers=HEADERS).status_code == 200
                points = fake.add_assignment(course, "Participation points", 2, group="Other", post_manually=True)
                fake.grade(course, points, fake.enrollments[course][0]["user_id"], 1)
                fake.add_assignment(course, "Bonus", None, group="Extra")  # Canvas gives it no points
                page.goto(f"{address}/#gradebook/{term}")
                heading = page.locator(".page-heading")
                start = heading.locator(".page-heading-secondary").get_by_role("button", name="Start from Canvas", exact=True)
                expect(start).to_have_attribute("data-outward", "")
                start.click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                expect(dialog).to_contain_text("MATH-140-507: Mathematics for Business", timeout=30000)
                # The summary above the button says what pressing Sync does: Quiz 1 comes in as a new column.
                expect(dialog.locator(".gbd-summary")).to_contain_text("Adds 2 new columns")
                # An assignment Canvas gives no points waits under Needs your choice (requirement, 2026-10-02); chosen as a new column it
                # stays there, named in the footnote, until it has points. Skipped here, the rest syncs.
                needs = dialog.locator('[data-keep="group-needs"]')
                expect(needs).to_contain_text("Bonus")
                expect(needs).to_contain_text("Canvas gives no points")
                expect(dialog.locator(".gbd-summary")).to_contain_text("1 Canvas assignment needs your choice of column")
                dialog.get_by_label("Where Bonus goes").select_option("new")
                expect(needs.locator("[data-new-points]")).to_have_value("", timeout=30000)
                expect(dialog.locator("[data-footnote]")).to_contain_text("Give “Bonus” its points under Needs your choice", timeout=30000)
                submit = dialog.locator('button[type="submit"]')
                expect(submit).to_be_disabled()
                expect(submit).to_have_attribute("data-outward", "")
                # Waiting on the instructor, not on Faculty: not-allowed, never the busy cursor.
                expect(submit).not_to_have_attribute("aria-busy", "true")
                expect(submit).to_have_css("cursor", "not-allowed")
                dialog.get_by_label("Where Bonus goes").select_option("skip")
                expect(needs).to_have_count(0, timeout=30000)
                expect(dialog.locator('[data-keep="group-import"]')).to_contain_text("New column in Quizzes")
                dialog.locator('[data-keep="group-import"]').get_by_role("button", name="Change").first.click()
                expect(dialog.get_by_label("Where Quiz 1 goes")).to_have_value("new")
                unlinked = dialog.locator('[data-keep="unlinked"]')
                unlinked.locator("summary").click()
                expect(unlinked).to_contain_text("Participation")
                expect(unlinked).to_contain_text("Otherwise Sync creates it in Canvas")
                unlinked.get_by_placeholder("Search Canvas assignments…").fill("partic")
                unlinked.get_by_role("button", name="Participation points").click()
                expect(dialog.locator('[data-keep^="group-"] tr', has_text="Participation points")).to_contain_text("Participation", timeout=30000)
                dialog.locator('[data-keep^="group-"] tr', has_text="Participation points").get_by_role("button", name="Change").click()
                expect(dialog.get_by_label("Where Participation points goes")).to_have_value(f"item:{participation}", timeout=30000)
                expect(dialog.locator('[data-grade-check="differs"]')).to_contain_text("1 of 1 grades differ")
                expect(dialog.locator('[data-keep="unlinked"]')).to_have_count(0)
                expect(dialog.locator("[data-new-category]")).to_have_value("new:Quizzes")
                expect(dialog).to_contain_text("1 grade not yet posted to students in Canvas")
                # Alice's typed 2 and Canvas's 1: held, open (a decision is never folded), Decide later by default.
                held = dialog.locator(".gbd-held")
                expect(held).to_contain_text("Example, Alice")
                expect(held.get_by_label("Example, Alice, Participation")).to_have_value("")
                expect(dialog.locator("[data-footnote]")).to_contain_text("1 grade changed in Canvas waits for your choice; the rest syncs.")
                # "Review what comes from Canvas" lists what Sync writes into Faculty (Alice's Quiz 1).
                changes = dialog.locator('[data-keep="changes"]')
                expect(changes.locator("summary .disclosure-hint")).to_have_text("1")
                changes.locator("summary").click()
                expect(changes.locator("[data-rows] tbody tr")).to_have_count(1)
                expect(changes.locator("[data-rows] tbody tr")).to_contain_text("Quiz 1")
                assert "001234567" not in dialog.inner_text()
                dialog.get_by_role("button", name="Sync · 1 grade from Canvas · 1 new column").click()
                expect(page.locator("#dialog")).to_be_hidden(timeout=30000)
                expect(page.locator(".gradebook-page")).to_contain_text("Quiz 1", timeout=20000)
                view = client.get(f"/api/gradebook/terms/{term}").json
                item = next(entry for entry in view["setup"]["items"] if entry["title"] == "Quiz 1")
                assert [cell["points"] for cell in view["cells"][item["id"]].values()] == ["9"], view["cells"]
                assert view["cells"][participation][alice_row]["points"] == "2"  # Faculty's, held: both sides keep theirs
                assert [c["name"] for c in view["setup"]["categories"]] == ["Quizzes"], view["setup"]["categories"]
                assert view["canvas"]["last_sync"]["held"] == 1, view["canvas"]
                # The page names the held grade and opens Sync with Canvas from the notice.
                expect(page.locator("[data-gradebook-sync]")).to_contain_text("1 grade changed in Canvas", timeout=20000)
                expect(heading.locator(".page-heading-secondary").get_by_role("button", name="Sync with Canvas", exact=True)).to_be_visible()
                page.goto(f"{address}/#course/{group['id']}/section/507?from=current-courses")
                expect(row).to_be_visible(timeout=20000)
                # Canvas refuses the kept session later: the next read opens the sign-in window again instead of failing.
                fake.sessions.clear()
                fake.seen["approved"] = False
                row.get_by_role("button", name="Compare roster…").click()
                expect(dialog.get_by_role("heading", name="Sign in to Canvas")).to_be_visible(timeout=20000)
                page.keyboard.press("Escape")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Canvas browser check passed")


if __name__ == "__main__":
    main()
