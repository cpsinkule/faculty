"""Late DR previews cannot repopulate another assignment or cleared sections.

Run with NVMe TMPDIR and a scratch working directory. Uses synthetic roster data;
no chart is created and no email transport runs.
"""
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from test_seating import SeatingTests


def main():
    fixture = SeatingTests("test_options_report_students_and_linked_dr_bookings")
    fixture.setUp()
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    options = fixture.client.get(
        f"/api/seating/options?assignment_id={fixture.assignment['id']}&sections={fixture.sections[507]['id']}"
    ).json
    held, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/seating/options?**", lambda route: held.append(route))
            page.goto(address + "/#seating")
            page.locator("#primary-action").click()
            dialog = page.locator("#seating-create")
            assignment = dialog.get_by_label("Exam assignment", exact=True)
            assignment.select_option(fixture.assignment["id"])
            room = dialog.get_by_label("Room for Section 507", exact=True)

            def start_preview():
                with page.expect_request("**/api/seating/options?**"):
                    room.select_option(fixture.hall["id"])

            def finish_preview():
                with page.expect_response("**/api/seating/options?**") as response:
                    held.pop(0).fulfill(status=200, json=options)
                assert response.value.json() == options
                page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

            start_preview()
            room.select_option("")
            finish_preview()
            expect(dialog.locator("#seating-forecast")).to_be_empty()
            expect(dialog.locator("#seating-dr")).to_be_empty()

            start_preview()
            assignment.select_option(fixture.quiz["id"])
            finish_preview()
            expect(dialog.locator("#seating-forecast")).to_be_empty()
            expect(dialog.locator("#seating-dr")).to_be_empty()
            expect(room).to_have_value("")

            assignment.select_option(fixture.assignment["id"])
            start_preview()
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            page.locator("#primary-action").click()
            expect(dialog).to_be_visible()
            finish_preview()
            expect(dialog.locator("#seating-forecast")).to_be_empty()
            expect(dialog.locator("#seating-dr")).to_be_empty()
            assert fixture.client.get("/api/seating").json == []
            assert errors == [], errors
            browser.close()
        print("PASS: cleared selections, assignment changes, and reopened creation dialogs ignore late DR previews; previews create no charts.")
    finally:
        server.close()
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
