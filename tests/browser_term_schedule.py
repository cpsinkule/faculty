"""The Term schedule in a real browser (SCHEDULING "TERM SCHEDULE", stage 1c): from the course Home's Schedule row, an empty schedule
offers "Import from your calendar" as its one primary action; the import reads each section's per-date class events and the office
hours into weekly rows (days, times, rooms, "Seats a makeup"); Save records revision 1; an edit is unsaved until saved again and keeps
the row's identity; the page fits the narrowest desktop window (1280px) under the CSP. Synthetic course and events only."""
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from rosters import connect


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        course = seed_course(client, sections=[507, 508])
        group = client.get("/api/state").json["course_groups"][0]
        with connect(app.config["DATABASE"]) as db:
            sections = {str(r[1]): r[0] for r in db.execute("SELECT id, number FROM course_sections")}
            term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
        calendar = app.extensions["calendar"]
        for day in ("2026-09-28", "2026-09-30", "2026-10-02", "2026-10-05", "2026-10-07", "2026-10-09"):
            calendar.create({"request_id": str(uuid.uuid4()), "title": "MATH 140 · Section 507", "date": day, "time": "08:00", "end_time": "08:50",
                             "all_day": False, "location": "HELD 107", "scope": "section", "definition_id": course["definition_id"], "term_id": term,
                             "section_id": sections["507"]})
        for day in ("2026-09-28", "2026-10-05"):
            calendar.create({"request_id": str(uuid.uuid4()), "title": "MATH 140 · Office hours", "date": day, "time": "15:00", "end_time": "17:00",
                             "all_day": False, "location": "BLOC 309", "scope": "term", "definition_id": course["definition_id"], "term_id": term})
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#course/{group['id']}/home")
                row = page.locator("[data-schedule-row]")
                expect(row).to_contain_text("Schedule")
                row.get_by_role("link", name="Term schedule: class meetings, office hours and meeting hours").click()
                expect(page.locator("#page-title")).to_have_text("Term schedule")
                expect(page.locator("#page-context")).to_be_hidden()  # the trail already names "MATH 140 · Fall 2026"
                primary = page.locator("#primary-action")
                expect(primary).to_have_text("Import from your calendar")
                expect(page.get_by_role("heading", name="Classes", exact=True)).to_be_visible()
                primary.click()
                expect(page.locator("#toast")).to_contain_text("Read 2 weekly times from 8 calendar events")
                expect(page.locator("[data-term=first_day]")).to_have_value("2026-09-28")
                expect(page.locator("[data-term=last_day]")).to_have_value("2026-10-09")
                classes = page.locator(".schedule-section").first
                expect(classes.locator("h4")).to_have_text("Section 507")
                checked = classes.locator(".schedule-days input:checked")
                expect(checked).to_have_count(3)  # Mon, Wed, Fri
                expect(classes.get_by_label("Room")).to_have_value("HELD 107")
                office = page.get_by_role("table", name="Office hours")
                expect(office.get_by_label("Seats a makeup")).not_to_be_checked()
                office.get_by_label("Seats a makeup").check()
                expect(primary).to_have_text("Save schedule")
                expect(page.locator("[data-schedule-status]")).to_have_text("Unsaved changes")
                page.screenshot(path=str(output / "term-schedule-import.png"), full_page=True)
                primary.click()
                expect(page.locator("#toast")).to_contain_text("Term schedule saved · revision 1.")
                expect(page.locator("[data-schedule-status]")).to_have_text("Saved")
                saved = client.get(f"/api/scheduling/terms/{term}").json
                assert saved["revision"] == 1 and [e["kind"] for e in saved["entries"]] == ["class", "office_hours"], saved
                assert saved["entries"][1]["seats_makeup"] is True, saved
                # An edit is unsaved until Save; the row keeps its identity.
                page.get_by_role("table", name="Section 507 class meetings").get_by_label("Starts").fill("08:05")
                expect(page.locator("[data-schedule-status]")).to_have_text("Unsaved changes")
                primary.click()
                expect(page.locator("#toast")).to_contain_text("revision 2")
                again = client.get(f"/api/scheduling/terms/{term}").json
                assert [e["entry"] for e in again["entries"]] == [e["entry"] for e in saved["entries"]], again
                assert again["entries"][0]["start"] == "08:05", again
                # Meeting hours: added with their own row; a second section's meeting too.
                page.get_by_role("button", name="Add meeting hours").click()
                expect(page.get_by_role("table", name="Meeting hours")).to_contain_text("Hosts makeups")
                page.locator(".schedule-section").nth(1).get_by_role("button", name="Add a class meeting").click()
                expect(page.get_by_role("table", name="Section 508 class meetings")).to_be_visible()
                primary.click()
                expect(page.locator("#toast")).to_contain_text("revision 3")
                # DAYS OFF: added by date and removed again; "Find days off" finds none here (every weekday has its events).
                page.get_by_label("Add a day off").fill("2026-10-02")
                page.get_by_role("button", name="Add", exact=True).click()
                expect(page.locator(".schedule-days-off")).to_contain_text("Oct 2")
                page.locator('[data-off-remove="2026-10-02"]').click()
                expect(page.locator(".schedule-days-off")).to_contain_text("None.")
                # USE THIS SCHEDULE: back to 8:00 so section 507 matches its events; then "Find days off" (it reads the saved schedule)
                # finds none, and the dry run says what is hidden and what is added.
                page.get_by_role("table", name="Section 507 class meetings").get_by_label("Starts").fill("08:00")
                primary.click()
                expect(page.locator("#toast")).to_contain_text("revision 4")
                page.get_by_role("button", name="Find days off from your calendar").click()
                expect(page.locator("#toast")).to_contain_text("No other days off")
                expect(primary).to_have_text("Use this schedule…")
                primary.click()
                dialog = page.locator("#schedule-use")
                expect(dialog.locator(".callout")).to_contain_text("hides 8 calendar events it replaces")
                expect(dialog).to_contain_text("Added: 6 dates without a calendar event today")  # section 508's new meetings
                page.screenshot(path=str(output / "term-schedule-use.png"))
                dialog.get_by_role("button", name="Use this schedule").click()
                expect(page.locator("#toast")).to_contain_text("8 copied events hidden")
                expect(page.locator("#page-subtitle")).to_contain_text("On your Calendar and Google since")
                expect(primary).to_be_hidden()
                page.goto(f"{address}/#calendar?month=2026-10&view=month&date=2026-10-05")
                expect(page.locator('.calendar-chip[data-kind="schedule"]').filter(has_text="Section 507").first).to_be_visible()
                expect(page.locator('.calendar-chip[data-kind="custom"]').filter(has_text="Section 507")).to_have_count(0)
                # An office-hours appointment over Monday's class says so (stage 1e: a warning, never a refusal).
                page.goto(f"{address}/#office-hours")
                page.get_by_role("button", name="Schedule office hours").first.click()
                form = page.locator("#support-form")
                form.get_by_label("Date").fill("2026-10-05")
                form.get_by_label("Start (campus time)").fill("08:15")
                # MEETING LENGTH (2026-10-06): End follows Start, keeping the form's 30 minutes.
                expect(form.locator('[name="end_time"]')).to_have_value("08:45")
                form.locator('[name="end_time"]').fill("08:45")
                form.locator('[name="end_time"]').dispatch_event("change")
                expect(form.locator("[data-appointment-check] .callout")).to_contain_text("Overlaps Section 507 (8:00–8:50 AM)")
                form.get_by_role("button", name="Cancel").click()
                page.goto(f"{address}/#course/{group['id']}/home/schedule")
                page.get_by_role("button", name="Stop using this schedule…").click()
                page.locator("#schedule-stop").get_by_role("button", name="Stop using").click()
                expect(page.locator("#toast")).to_contain_text("8 shown again")
                expect(primary).to_have_text("Use this schedule…")
                page.set_viewport_size({"width": 1280, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "the page scrolls sideways at 1280px"
                page.screenshot(path=str(output / "term-schedule-1280.png"), full_page=True)
                assert not errors, errors
                assert page.evaluate("window.cspViolations") == []
                browser.close()
        finally:
            server.close()
    print("Term schedule passed: course Home row, import from the calendar, Seats a makeup, save, edits keep identities, days off, Use with its dry run, the Calendar, Stop using, 1280px and CSP.")


if __name__ == "__main__":
    main()
