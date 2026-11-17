"""TA contacts in a real browser (AGENTS.md "TA contacts"): a section tab's TAs row starts empty; Add TAs… copies another section's
TA and adds one more; the row then offers each name as an email to that TA, Email all and Copy emails; Edit TAs… removes one. A
stale window is refused. Checked at 1440px and at the narrowest desktop width (1280px). Synthetic data only."""
import os
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
from rosters import connect
from test_storage import HEADERS


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        seed_course(client, code="MATH 140", sections=[507, 508])
        group = client.get("/api/state").json["course_groups"][0]
        with connect(app.config["DATABASE"]) as db:
            sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}
        sam = {"name": "Sam Park", "email": "spark@tamu.edu", "phone": "979-555-0111", "note": "Grader"}
        answer = client.post(f"/api/teaching-assistants/sections/{sections[508]}", json={"contacts": [sam], "base_revision": 0, "request_id": "seed"}, headers=HEADERS)
        assert answer.status_code == 200, answer.json
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=address)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(f"{address}/#course/{group['id']}/section/507?from=current-courses")
                row = page.locator("[data-ta-row]")
                expect(row).to_be_visible(timeout=20000)
                expect(row).to_contain_text("No TAs added")
                row.get_by_role("button", name="Add TAs…").click()
                dialog = page.locator("#dialog")
                expect(dialog.get_by_role("heading", name="TAs")).to_be_visible(timeout=20000)
                expect(dialog).to_contain_text("Section 507 · Fall 2026")
                dialog.get_by_role("button", name="Section 508 · 1 TA").click()
                expect(dialog.locator("[data-ta-copied]")).to_have_text("Added 1 TA from Section 508. Save to keep them here.")
                expect(dialog.get_by_label("Name")).to_have_value("Sam Park")
                dialog.get_by_role("button", name="Add another TA").click()
                second = dialog.locator("[data-ta-edit-row]").nth(1)
                expect(second.get_by_label("Name")).to_be_focused()
                second.get_by_label("Name").fill("Jordan Lee")
                second.get_by_label("Email").fill("jlee@tamu")
                dialog.get_by_role("button", name="Save").click()
                expect(dialog.locator("#form-error")).to_contain_text("Jordan Lee's email address does not look like one", timeout=20000)
                second.get_by_label("Email").fill("jlee@tamu.edu")
                second.get_by_label("Phone").fill("979-555-0100")
                second.get_by_label("Note").fill("Office hours Tue 2–4")
                dialog.get_by_role("button", name="Save").click()
                expect(dialog).to_be_hidden(timeout=20000)
                # The row: each name emails that TA, with the section in the subject; Email all; Copy emails.
                expect(row.get_by_role("link", name="Email Jordan Lee")).to_have_attribute("href", "mailto:jlee@tamu.edu?subject=MATH%20140%20Section%20507", timeout=20000)
                expect(row.get_by_role("link", name="Email Sam Park")).to_have_attribute("href", "mailto:spark@tamu.edu?subject=MATH%20140%20Section%20507")
                expect(row).to_contain_text("jlee@tamu.edu · 979-555-0100 · Office hours Tue 2–4")
                expect(row.get_by_role("link", name="Email all 2 TAs")).to_have_attribute("href", "mailto:spark@tamu.edu,jlee@tamu.edu?subject=MATH%20140%20Section%20507")
                row.get_by_role("button", name="Copy emails").click()
                expect(page.locator("#toast")).to_contain_text("Copied 2 email addresses.")
                assert page.evaluate("navigator.clipboard.readText()") == "spark@tamu.edu, jlee@tamu.edu"
                saved = client.get(f"/api/teaching-assistants/sections/{sections[507]}").json
                assert (saved["revision"], [ta["name"] for ta in saved["contacts"]]) == (1, ["Sam Park", "Jordan Lee"]), saved
                assert client.get(f"/api/teaching-assistants/sections/{sections[508]}").json["contacts"] == [sam]  # 508 unchanged
                # Edit: remove Sam. A window left open while the list changed elsewhere is refused and says so.
                row.get_by_role("button", name="Edit TAs…").click()
                expect(dialog.locator("[data-ta-edit-row]")).to_have_count(2)
                dialog.get_by_role("button", name="Remove TA 1").click()
                expect(dialog.locator("[data-ta-edit-row]")).to_have_count(1)
                expect(dialog.get_by_label("Name")).to_have_value("Jordan Lee")
                other = client.post(f"/api/teaching-assistants/sections/{sections[507]}", json={"contacts": saved["contacts"] + [{"name": "Riley Chen", "email": "rchen@tamu.edu"}], "base_revision": 1, "request_id": "elsewhere"}, headers=HEADERS)
                assert other.status_code == 200, other.json
                dialog.get_by_role("button", name="Save").click()
                expect(dialog.locator("#form-error")).to_contain_text("changed elsewhere", timeout=20000)
                dialog.get_by_role("button", name="Cancel").click()
                page.reload()
                expect(row.get_by_role("link", name="Email Riley Chen")).to_be_visible(timeout=20000)
                row.get_by_role("button", name="Edit TAs…").click()
                dialog.get_by_role("button", name="Remove TA 1").click()
                dialog.get_by_role("button", name="Save").click()
                expect(dialog).to_be_hidden(timeout=20000)
                expect(row.get_by_role("link", name="Email Sam Park")).to_have_count(0, timeout=20000)
                expect(row.get_by_role("link", name="Email all 2 TAs")).to_be_visible()
                # The narrowest desktop width (DESIGN-LANGUAGE, 2026-10-03): the row fits without scrolling the page sideways.
                page.set_viewport_size({"width": 1280, "height": 900})
                expect(row.get_by_role("link", name="Email Riley Chen")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "the page scrolls sideways at 1280px"
                # The two refusals this check provokes (a bad address, a stale window) are the only failed requests.
                refused = [e for e in errors if e.startswith("Failed to load resource: the server responded with a status of")]
                assert refused == [f"Failed to load resource: the server responded with a status of {code}" for code in ("400 (BAD REQUEST)", "409 (CONFLICT)")], refused
                assert not [e for e in errors if e not in refused], errors
                browser.close()
        finally:
            server.close()
    print("TA contacts browser check passed")


if __name__ == "__main__":
    main()
