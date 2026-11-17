"""The course Home's Sync grades in a real browser (docs/decisions/canvas.md SYNC WITH CANVAS, "SYNC EVERY SECTION"; instructor,
2026-10-06: "add a sync button to the course homepage that automatically syncs all of them (keep the syncs as they are on the
individual pages still)"). Two sections, each linked to its own Canvas course: a section tab's first Sync with Canvas still ticks
its own course alone, and the course Home's Sync grades opens the same dialog with both ticked and sends both. Synthetic data only,
against the loopback FakeCanvas."""
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from canvas_checks import canvas_linked_app
from fake_canvas import FakeCanvas
from test_storage import HEADERS

ALICE, BOB, CAROL = "001234567", "123456789", "222333444"


def main():
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        course_a = fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026C", "Fall 2026")
        course_b = fake.add_course("MATH-140-508: Mathematics for Business", "MATH-140-508-2026C", "Fall 2026")
        section_a, section_b = fake.add_section(course_a, "507"), fake.add_section(course_b, "508")
        alice = fake.add_student(course_a, section_a, "Example, Alice", ALICE)
        bob = fake.add_student(course_a, section_a, "Example, Bob", BOB)
        carol = fake.add_student(course_b, section_b, "Example, Carol", CAROL)
        quiz_a = fake.add_assignment(course_a, "Quiz 1", 10, group="Quizzes")
        quiz_b = fake.add_assignment(course_b, "Quiz 1", 10, group="Quizzes")
        fake.grade(course_a, quiz_a, alice, 9)
        app, client, group, term, base = canvas_linked_app(directory, fake, course_a, course_b)
        saved = client.post(base + "/setup", json={"base_revision": None, "request_id": "setup-1", "setup": {
            "categories": [{"ref": "q", "name": "Quizzes"}], "items": [{"title": "Quiz 1", "points": "10", "category_id": "q"}]}}, headers=HEADERS)
        assert saved.status_code == 200, saved.json
        quiz = saved.json["setup"]["items"][0]["id"]
        rows_by_name = {student["name"]: student["row"] for student in saved.json["students"]}
        # Faculty's grades for Bob (507) and Carol (508): Canvas has none, so a sync of both courses sends both.
        typed = client.post(base + "/scores", json={"request_id": "typed-1", "changes": [
            {"item_id": quiz, "row": rows_by_name["Example, Bob"], "base": None, "status": "scored", "points": "8"},
            {"item_id": quiz, "row": rows_by_name["Example, Carol"], "base": None, "status": "scored", "points": "7"}]}, headers=HEADERS)
        assert typed.status_code == 200, typed.json
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                dialog = page.locator("#dialog")
                # A section tab's Sync stays as it was: the term's first sync there ticks that section's course alone.
                page.goto(f"{address}/#course/{group['id']}/section/507")
                heading = page.locator(".page-heading")
                heading.locator(".page-heading-secondary").get_by_role("button", name="Start from Canvas", exact=True).click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                expect(dialog.locator(f'[data-sync-course="{course_a}"]')).to_be_checked(timeout=30000)
                expect(dialog.locator(f'[data-sync-course="{course_b}"]')).not_to_be_checked()
                dialog.get_by_role("button", name="Cancel").click()
                expect(dialog).to_be_hidden()
                # The course Home's Canvas row: Sync grades, an outward action, once a section links a Canvas course.
                page.goto(f"{address}/#course/{group['id']}/home")
                sync = page.locator("[data-canvas-home-row] [data-course-canvas-sync]")
                expect(sync).to_have_text("Sync grades", timeout=20000)
                expect(sync).to_have_attribute("data-outward", "")
                expect(sync).to_have_attribute("aria-label", "Sync every section’s grades with Canvas")
                sync.click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                expect(dialog.locator(f'[data-sync-course="{course_a}"]')).to_be_checked(timeout=30000)
                expect(dialog.locator(f'[data-sync-course="{course_b}"]')).to_be_checked()
                expect(sync).not_to_have_attribute("aria-busy", "true")  # busy only until the dialog opened
                submit = dialog.locator('button[type="submit"]')
                expect(submit).to_have_text("Sync · 2 grades to Canvas · 1 grade from Canvas", timeout=30000)
                submit.click()
                expect(dialog).to_contain_text("2 grades now show in Canvas", timeout=30000)
                assert fake.submissions[course_a][(bob, quiz_a)]["score"] == 8.0, fake.submissions[course_a]
                assert fake.submissions[course_b][(carol, quiz_b)]["score"] == 7.0, fake.submissions[course_b]
                view = client.get(base).json
                assert view["cells"][quiz][rows_by_name["Example, Alice"]]["points"] == "9", view["cells"][quiz]  # filled from Canvas
                assert view["canvas"]["sent_before"] is True, view["canvas"]
                dialog.get_by_role("button", name="Close", exact=True).click()  # the footer's: the header's is "Close dialog"
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Course Home sync browser check passed")


def focus_check():
    """The dialog's preview is drawn again after every answer and keeps the control that had keyboard focus (FRONTEND.md quiet
    refresh): a held grade's choice, a Publish tick, a course tick and a row's Change button, each operated by keyboard."""
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        course_a = fake.add_course("MATH-140-507: Mathematics for Business", "MATH-140-507-2026C", "Fall 2026")
        course_b = fake.add_course("MATH-140-508: Mathematics for Business", "MATH-140-508-2026C", "Fall 2026")
        section_a, section_b = fake.add_section(course_a, "507"), fake.add_section(course_b, "508")
        alice = fake.add_student(course_a, section_a, "Example, Alice", ALICE)
        fake.add_student(course_a, section_a, "Example, Bob", BOB)
        fake.add_student(course_b, section_b, "Example, Carol", CAROL)
        quiz_a = fake.add_assignment(course_a, "Quiz 1", 10, group="Quizzes")
        fake.add_assignment(course_b, "Quiz 1", 10, group="Quizzes")
        fake.add_assignment(course_a, "Quiz 2", 10, group="Quizzes", published=False)  # a column Canvas has unpublished
        fake.grade(course_a, quiz_a, alice, 9)
        app, client, group, term, base = canvas_linked_app(directory, fake, course_a, course_b)
        saved = client.post(base + "/setup", json={"base_revision": None, "request_id": "setup-1", "setup": {
            "categories": [{"ref": "q", "name": "Quizzes"}], "items": [
                {"title": "Quiz 1", "points": "10", "category_id": "q"}, {"title": "Quiz 2", "points": "10", "category_id": "q"}]}}, headers=HEADERS)
        assert saved.status_code == 200, saved.json
        quiz, quiz2 = (item["id"] for item in saved.json["setup"]["items"])
        rows_by_name = {student["name"]: student["row"] for student in saved.json["students"]}
        # Faculty's 4 for Alice differs from Canvas's 9: a held grade; Quiz 2 has a grade to publish.
        typed = client.post(base + "/scores", json={"request_id": "typed-1", "changes": [
            {"item_id": quiz, "row": rows_by_name["Example, Bob"], "base": None, "status": "scored", "points": "8"},
            {"item_id": quiz, "row": rows_by_name["Example, Carol"], "base": None, "status": "scored", "points": "7"},
            {"item_id": quiz, "row": rows_by_name["Example, Alice"], "base": None, "status": "scored", "points": "4"},
            {"item_id": quiz2, "row": rows_by_name["Example, Bob"], "base": None, "status": "scored", "points": "5"}]}, headers=HEADERS)
        assert typed.status_code == 200, typed.json
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                dialog = page.locator("#dialog")
                page.goto(f"{address}/#course/{group['id']}/home")
                sync = page.locator("[data-canvas-home-row] [data-course-canvas-sync]")
                expect(sync).to_have_text("Sync grades", timeout=20000)
                sync.click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                expect(dialog.locator(f'[data-sync-course="{course_b}"]')).to_be_checked(timeout=30000)
                expect(dialog.locator("select[data-held]").first).to_be_visible(timeout=30000)
                expect(dialog.locator("[data-publish]").first).to_be_visible()
                expect(dialog.locator("[data-row-edit]").first).to_be_visible()
                kept = lambda attribute: page.evaluate(
                    "(attribute) => { const node = document.activeElement; return Boolean(node && node.closest('#dialog') && node.hasAttribute(attribute)); }", attribute)

                held = dialog.locator("select[data-held]").first
                held.focus()
                before = held.input_value()
                page.keyboard.press("ArrowDown")
                expect(dialog.locator("select[data-held]").first).not_to_have_value(before)
                assert kept("data-held"), "a held grade's choice keeps keyboard focus"

                publish = dialog.locator("[data-publish]").first
                publish.focus()
                was = publish.is_checked()
                page.keyboard.press("Space")
                expect(dialog.locator("[data-publish]").first).to_be_checked(checked=not was)
                assert kept("data-publish"), "a Publish tick keeps keyboard focus"

                tick = dialog.locator(f'[data-sync-course="{course_b}"]')
                tick.focus()
                page.keyboard.press("Space")
                expect(dialog.locator(f'[data-sync-course="{course_b}"]')).not_to_be_checked(timeout=30000)
                page.wait_for_timeout(1500)
                assert kept("data-sync-course"), "a course tick keeps keyboard focus"

                change = dialog.locator("[data-row-edit]").first
                change.focus()
                page.keyboard.press("Enter")
                expect(dialog.locator("[data-row-edit]").first).to_have_text("Done")
                assert kept("data-row-edit"), "a row's Change button keeps keyboard focus"
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Sync dialog keyboard focus check passed")


if __name__ == "__main__":
    main()
    focus_check()
