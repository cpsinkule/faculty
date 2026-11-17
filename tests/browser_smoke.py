"""End-to-end checks with an isolated database; never changes the real workspace.

Optional dependency: pip install playwright && playwright install chromium
Run from project root: python tests/browser_smoke.py
"""

import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server
from course_form_checks import check_course_form
from course_code_checks import fill_course_code, check_course_codes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server = create_server(create_app(directory), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(address)
                expect(page.get_by_role("heading", name="Needs you", exact=True)).to_be_visible()
                # Legacy tool routes remain available without sidebar entries.
                page.evaluate("location.hash = 'tools'")
                expect(page.get_by_role("heading", name="Course Rosters", exact=True)).to_be_visible()
                expect(page.locator("#stat-total")).to_have_text("1")
                expect(page.locator("#stat-ready")).to_have_text("0")
                page.screenshot(path=str(output / "desktop.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

                # Exercise a complete tool lifecycle from a real browser.
                page.get_by_role("button", name="Add a tool", exact=True).click()
                page.get_by_label("Tool name", exact=True).fill("Roster Test App")
                page.get_by_label("Description", exact=False).fill("Students organized by course.")
                page.get_by_label("Stage", exact=True).select_option("ready")
                page.get_by_label("Tool URL", exact=False).fill("https://roster.example.ts.net")
                page.get_by_label("Pin this tool for easy access").check()
                page.get_by_role("button", name="Add tool", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.get_by_role("heading", name="Roster Test App", exact=True)).to_be_visible()
                expect(page.locator("#stat-ready")).to_have_text("1")
                expect(page.get_by_role("link", name="Open Roster Test App in a new tab")).to_have_attribute("href", "https://roster.example.ts.net")

                # Another browser session sees server-persisted changes.
                second = browser.new_page()
                second.goto(address + "/#tools")
                expect(second.get_by_role("heading", name="Roster Test App", exact=True)).to_be_visible()
                second.close()
                page.reload()
                expect(page.get_by_role("heading", name="Roster Test App", exact=True)).to_be_visible()

                page.keyboard.press("/")
                expect(page.get_by_role("searchbox")).to_be_focused()
                page.get_by_role("searchbox").fill("nonexistent tool")
                expect(page.get_by_role("heading", name="No tools found.")).to_be_visible()
                page.get_by_role("button", name="Clear filters").click()
                page.get_by_role("button", name="Ready", exact=True).click()
                expect(page.locator(".tool-card")).to_have_count(1)
                page.get_by_role("button", name="All tools", exact=True).click()
                page.get_by_role("button", name="Unpin Roster Test App", exact=True).click()
                expect(page.get_by_role("button", name="Pin Roster Test App", exact=True)).to_be_visible()
                page.evaluate("location.hash = 'pinned'")
                expect(page.locator(".tool-card")).to_have_count(1)
                expect(page.get_by_role("heading", name="Course Rosters", exact=True)).to_be_visible()
                # With nothing pinned, "Browse your tools" goes to the tools list, which Home no longer draws.
                page.get_by_role("button", name="Unpin Course Rosters", exact=True).click()
                expect(page.get_by_role("heading", name="Your favorites belong here.")).to_be_visible()
                page.get_by_role("button", name="Browse your tools", exact=True).click()
                expect(page.locator("#page-title")).to_have_text("Your tools")
                expect(page.locator(".tool-card")).to_have_count(2)
                page.get_by_role("button", name="Pin Course Rosters", exact=True).click()
                # Only the tools page's own stat tiles and filters set its status filter: another page's data-status
                # marker (an absence's decision, a bubble-sheet row) does not reach it.
                page.evaluate("document.body.insertAdjacentHTML('beforeend', '<span id=\"status-probe\" data-status=\"ready\">probe</span>')")
                page.locator("#status-probe").dispatch_event("click")
                expect(page.locator(".filter[data-status=all]")).to_have_attribute("aria-pressed", "true")
                expect(page.locator(".filter[data-status=ready]")).to_have_attribute("aria-pressed", "false")
                page.get_by_role("button", name="Ready", exact=True).click()
                expect(page.locator(".filter[data-status=ready]")).to_have_attribute("aria-pressed", "true")
                page.get_by_role("button", name="All tools", exact=True).click()
                page.evaluate("document.getElementById('status-probe').remove()")
                page.evaluate("location.hash = 'roadmap'")
                expect(page.locator(".roadmap-column")).to_have_count(3)
                page.evaluate("location.hash = 'tools'")

                page.get_by_role("button", name="Edit Roster Test App", exact=True).click()
                page.get_by_label("Stage", exact=True).select_option("building")
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.locator("#stat-building")).to_have_text("1")
                page.get_by_role("button", name="Edit Roster Test App", exact=True).click()
                page.get_by_role("button", name="Remove", exact=True).click()
                page.get_by_role("button", name="Keep tool", exact=True).click()
                page.get_by_role("button", name="Remove", exact=True).click()
                page.get_by_role("button", name="Remove tool", exact=True).click()
                expect(page.get_by_role("heading", name="Roster Test App", exact=True)).to_have_count(0)

                page.locator('.sidebar [data-action="settings"]').click()
                page.get_by_label("Workspace name", exact=True).fill("Fall teaching")
                page.get_by_label("Your name", exact=False).fill("Alex")
                page.get_by_role("button", name="Save changes", exact=True).click()
                page.locator(".sidebar").get_by_role("link", name="Home", exact=True).click()
                expect(page.get_by_role("heading", name="Welcome back, Alex.", exact=True)).to_be_visible()
                page.locator('.sidebar [data-action="settings"]').click()
                with page.expect_download() as download_info:
                    page.get_by_role("link", name="Export", exact=True).click()
                assert download_info.value.suggested_filename == "faculty-workspace.json"
                page.get_by_role("button", name="Cancel", exact=True).click()

                page.evaluate("location.hash = 'tools'")

                # Text from the server is never interpreted as HTML.
                page.get_by_role("button", name="Edit Course Rosters", exact=True).click()
                page.get_by_label("Tool name", exact=True).fill('<img src=x onerror="alert(1)">')
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("heading", name='<img src=x onerror="alert(1)">', exact=True)).to_be_visible()
                expect(page.locator(".tool-card img")).to_have_count(0)

                # Reset the screenshot fixture to the actual first-run appearance.
                page.get_by_role("button", name='Edit <img src=x onerror="alert(1)">', exact=True).click()
                page.get_by_label("Tool name", exact=True).fill("Course Rosters")
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                page.locator('.sidebar [data-action="settings"]').click()
                page.get_by_label("Workspace name", exact=True).fill("My teaching workspace")
                page.get_by_label("Your name", exact=False).fill("")
                page.get_by_role("button", name="Save changes", exact=True).click()
                page.locator(".sidebar").get_by_role("link", name="Home", exact=True).click()
                expect(page.get_by_role("heading", name="Your teaching day.", exact=True)).to_be_visible()
                page.locator("#toast").wait_for(state="hidden")

                # Desktop only (requirement, 2026-10-03): the narrowest supported window keeps every page on screen.
                page.set_viewport_size({"width": 1280, "height": 800})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Layout overflow at 1280"
                page.screenshot(path=str(output / "desktop-1280.png"), full_page=True)
                page.evaluate("location.hash = 'tools'")
                page.get_by_role("button", name="Add a tool", exact=True).click()
                expect(page.get_by_role("dialog")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "desktop-form-1280.png"), full_page=True)
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                page.set_viewport_size({"width": 1440, "height": 1080})
                # The Connection guide is a section of Workspace settings.
                page.locator('.sidebar [data-action="settings"]').click()
                page.get_by_role("dialog").locator("#settings-access > summary").click()
                expect(page.get_by_role("heading", name="Your workspace, anywhere.")).to_be_visible()
                page.get_by_role("button", name="Close dialog", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()

                # Course tabs show real persisted courses, with a current-only view: one Courses entry in the sidebar,
                # and the page's own Current | All switch.
                page.set_viewport_size({"width": 1440, "height": 1080})
                page.locator(".sidebar").get_by_role("link", name="Courses", exact=True).click()
                expect(page.get_by_role("heading", name="Current courses", exact=True)).to_be_visible()
                expect(page.locator(".sidebar").get_by_role("link", name="Courses", exact=True)).to_have_attribute("aria-current", "page")
                page.get_by_role("link", name="All courses", exact=True).click()
                expect(page.locator(".sidebar").get_by_role("link", name="Courses", exact=True)).to_have_attribute("aria-current", "page")
                expect(page.get_by_role("link", name="All courses", exact=True)).to_have_attribute("aria-current", "page")
                expect(page.get_by_role("heading", name="All courses", exact=True)).to_be_visible()
                expect(page.locator(".tools-section")).not_to_be_visible()
                expect(page.get_by_role("heading", name="Your courses start here.")).to_be_visible()
                for name, code, term, year, current in [
                    ("Introduction to Biology", "BIO 101", "Fall", 2026, True),
                    ("Ecology", "BIO 201", "Spring", 2025, False),
                ]:
                    page.locator("#primary-action").click()
                    page.get_by_label("Course name", exact=True).fill(name)
                    fill_course_code(page, code)
                    page.get_by_label("Term", exact=True).select_option(term)
                    page.get_by_role("spinbutton", name="Year", exact=True).fill(str(year))
                    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("1")
                    page.get_by_label("Current course", exact=True).set_checked(current)
                    page.get_by_role("button", name="Add course", exact=True).click()
                    expect(page.get_by_role("dialog")).not_to_be_visible()
                    expect(page.locator(".course-card").get_by_text(name, exact=True)).to_be_visible()
                expect(page.locator(".course-card")).to_have_count(2)
                expect(page.locator("#all-courses-count")).to_have_text("2")
                expect(page.locator("#current-courses-count")).to_have_text("1")
                check_course_form(page, output)
                check_course_codes(page, output)
                page.screenshot(path=str(output / "all-courses-desktop.png"), full_page=True)
                page.get_by_role("link", name="Current courses", exact=True).click()
                expect(page.locator(".course-card")).to_have_count(1)
                expect(page.locator(".course-card").get_by_text("Ecology", exact=True)).to_have_count(0)
                page.reload()
                expect(page.get_by_role("heading", name="Current courses", exact=True)).to_be_visible()
                expect(page.locator(".course-card")).to_have_count(1)
                page.keyboard.press("/")
                expect(page.get_by_role("searchbox", name="Find a course")).to_be_focused()
                page.get_by_role("searchbox", name="Find a course").fill("no matching course")
                expect(page.get_by_role("heading", name="No courses found.")).to_be_visible()
                page.get_by_role("button", name="Clear search", exact=True).click()
                page.get_by_role("button", name="Edit Introduction to Biology", exact=True).click()
                page.get_by_label("Current course", exact=True).uncheck()
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("heading", name="No current courses yet.")).to_be_visible()
                page.get_by_role("button", name="Browse all courses", exact=True).click()
                expect(page.locator(".course-card")).to_have_count(2)
                second = browser.new_page()
                second.goto(address + "/#all-courses")
                expect(second.locator(".course-card")).to_have_count(2)
                expect(second.locator("#current-courses-count")).to_have_text("0")
                second.close()
                page.get_by_role("button", name="Edit Introduction to Biology", exact=True).click()
                page.get_by_label("Current course", exact=True).check()
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.locator("#current-courses-count")).to_have_text("1")
                page.get_by_role("button", name="Edit Ecology", exact=True).click()
                page.get_by_role("button", name="Remove", exact=True).click()
                page.get_by_role("button", name="Keep course", exact=True).click()
                page.get_by_role("button", name="Remove", exact=True).click()
                page.get_by_role("button", name="Remove course", exact=True).click()
                expect(page.locator(".course-card")).to_have_count(1)
                page.set_viewport_size({"width": 1280, "height": 800})
                page.get_by_role("link", name="Current courses", exact=True).click()
                expect(page.locator(".course-card")).to_have_count(1)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Course view overflow"
                page.screenshot(path=str(output / "current-courses-1280.png"), full_page=True)
                page.get_by_role("link", name="All courses", exact=True).click()
                page.get_by_role("link", name="Current courses", exact=True).click()
                expect(page.get_by_role("heading", name="Current courses", exact=True)).to_be_visible()
                assert errors == [], f"Browser errors: {errors}"
                browser.close()
                print("Browser checks passed: tools and courses CRUD, current-course filtering, cross-device persistence, search, pinning, roadmap, settings, export, escaping, and the 1280 px layout.")
        finally:
            server.close()


if __name__ == "__main__":
    main()
