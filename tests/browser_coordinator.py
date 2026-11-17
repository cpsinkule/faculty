"""COORDINATOR in the browser, against an isolated temporary workspace: the course card's Coordinated course box with its Name
and Email greyed out until it is ticked; the saved coordinator read back when Course settings opens; and a coordinator's
follow-up on the Follow-up page, named as the coordinator and prepared as cards (a scripted engine; nothing is sent).

Run from the project root: python tests/browser_coordinator.py
"""
import logging
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from course_code_checks import fill_course_code
from test_coordinator_followups import ASKED, CoordinatorTests
from test_followup_suggestions import ScriptedEngine, proposing


def check_course_card(page, address, shots):
    page.goto(address + "/#all-courses")
    page.locator("#primary-action").click()
    dialog = page.get_by_role("dialog")
    box, name, email = dialog.get_by_label("Coordinated course", exact=True), dialog.get_by_label("Name", exact=True), dialog.get_by_label("Email", exact=True)
    # Greyed out until the box is ticked.
    expect(box).not_to_be_checked()
    expect(name).to_be_disabled()
    expect(email).to_be_disabled()
    box.scroll_into_view_if_needed()
    email.scroll_into_view_if_needed()
    page.wait_for_timeout(400)   # the window's entrance has finished before the picture
    page.screenshot(path=str(shots / "coordinator-card-unticked.png"))
    box.check()
    expect(name).to_be_enabled()
    expect(name).to_be_focused()
    expect(email).to_be_enabled()
    box.uncheck()
    expect(name).to_be_disabled()
    box.check()
    dialog.get_by_label("Course name", exact=True).fill("Calculus III")
    fill_course_code(page, "MATH 251")
    dialog.get_by_label("Term", exact=True).select_option("Fall")
    dialog.get_by_role("spinbutton", name="Year", exact=True).fill("2026")
    dialog.get_by_role("spinbutton", name="Section 1", exact=True).fill("501")
    # Ticked, both are needed: the card does not save without them.
    dialog.get_by_role("button", name="Add course", exact=True).click()
    expect(dialog).to_be_visible()
    name.fill("Dr. Morgan Reyes")
    email.fill("mreyes@tamu.edu")
    page.screenshot(path=str(shots / "coordinator-card-ticked.png"))
    dialog.get_by_role("button", name="Add course", exact=True).click()
    expect(dialog).not_to_be_visible()
    # Course settings reads the term's coordinator back.
    page.get_by_role("link", name="Open MATH 251 · Fall 2026", exact=True).click()
    page.locator(".page-heading-secondary").get_by_role("button", name="Course settings", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_label("Coordinated course", exact=True)).to_be_checked()
    expect(dialog.get_by_label("Name", exact=True)).to_have_value("Dr. Morgan Reyes")
    expect(dialog.get_by_label("Email", exact=True)).to_have_value("mreyes@tamu.edu")
    # Unticking greys them out again and saves a course that is not coordinated.
    dialog.get_by_label("Coordinated course", exact=True).uncheck()
    expect(dialog.get_by_label("Name", exact=True)).to_be_disabled()
    dialog.get_by_role("button", name="Save changes", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.locator(".page-heading-secondary").get_by_role("button", name="Course settings", exact=True).click()
    expect(page.get_by_role("dialog").get_by_label("Coordinated course", exact=True)).not_to_be_checked()
    page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()


def check_follow_up(page, address, shots):
    page.goto(address + "/#follow-ups")
    root = page.locator("#student-support-section")
    row = root.locator(".support-queue-row", has_text="Dr. Jordan Lee")
    expect(row).to_have_count(1)
    expect(row.locator(".support-queue-meta")).to_have_text("MATH 140 · Coordinator")
    row.click()
    review = root.locator(".support-review")
    expect(review.locator(".support-review-head .field-hint").first).to_have_text("MATH 140 · Fall 2026 · Course coordinator")
    expect(review.locator(".support-review-person")).to_have_text("Dr. Jordan Lee")
    expect(review.locator("[data-support-record]")).to_have_count(0)   # no student record to open
    expect(review.get_by_role("heading", name="Coordinator’s message", exact=True)).to_be_visible()
    expect(review.locator(".support-message-text")).to_have_text(ASKED)
    expect(review.get_by_role("button", name="Write to coordinator…", exact=True)).to_be_visible()
    expect(review.get_by_role("button", name="Write to student…", exact=True)).to_have_count(0)
    expect(review.get_by_role("button", name="Join to another follow-up…", exact=True)).to_have_count(0)
    # The Assistant's steps wait as cards; the status step names the coordinator.
    expect(review).to_contain_text("Mark this conversation To do")
    expect(review).not_to_contain_text("Done for you")
    page.screenshot(path=str(shots / "coordinator-follow-up.png"), full_page=True)
    # Write to coordinator… opens Mail's compose window to the address on the course card, in their thread.
    review.get_by_role("button", name="Write to coordinator…", exact=True).click()
    compose = page.get_by_role("dialog")
    expect(compose).to_contain_text("To Dr. Jordan Lee, coordinator of MATH 140")
    expect(compose.locator('[name="to"]')).to_have_value("jlee@tamu.edu")
    page.keyboard.press("Escape")


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    shots = Path("test-results")
    shots.mkdir(exist_ok=True)
    fixture = CoordinatorTests("test_a_course_no_longer_coordinated_is_not_prepared")
    fixture.setUp()
    server = None
    try:
        fixture.fake_mail()
        fixture.wrote(seconds=1)
        todo = ("/api/student-support", lambda c: {"enrollment_id": c.run["enrollment_id"], "kind": "followup", "source_key": c.run["source_key"],
                                                   "title": "Final exam proctors", "state": "todo", "next": "Send both proctors’ names by Monday"},
                "Mark it To do")
        # The scripted engine stands in for the whole run: the page's own reads ask whether a model is available.
        engine = patch("assistant.conversations.engine_for", side_effect=lambda _: ScriptedEngine(proposing(todo)))
        engine.start()
        fixture.addCleanup(engine.stop)
        assert fixture.suggestions.process_one()
        assert fixture.suggestions.dispatch_one()
        fixture.wait()
        server = create_server(fixture.app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            check_follow_up(page, address, shots)
            check_course_card(page, address, shots)
            assert not errors, errors
            browser.close()
        print("Coordinator browser checks passed.")
    finally:
        if server:
            server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
