"""PUBLISH FROM SYNC in a real browser (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-07). A column whose Canvas assignment is
not published is ticked under "Publish in Canvas, then send" in the Sync summary; unticked it is named under Can't sync; ticked,
Sync publishes it first and then sends its grade. Synthetic data only, against the loopback FakeCanvas."""
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
        fake.add_student(course_a, section_a, "Example, Bob", BOB)
        carol = fake.add_student(course_b, section_b, "Example, Carol", CAROL)
        quiz_a = fake.add_assignment(course_a, "Quiz 1", 10, group="Quizzes")
        quiz_b = fake.add_assignment(course_b, "Quiz 1", 10, group="Quizzes")
        fake.grade(course_a, quiz_a, alice, 9)
        fake.grade(course_b, quiz_b, carol, 7)
        app, client, group, term, base = canvas_linked_app(directory, fake, course_a, course_b)
        # Start from Canvas: Quiz 1 becomes a column linked to both courses' assignment, with Canvas's grades.
        plan = client.post(base + "/canvas-sync/preview", json={"options": {}}, headers=HEADERS).json
        started = client.post(base + "/canvas-sync", json={"options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": "start-sync-1",
                                                           "confirmation": "sync"}, headers=HEADERS)
        assert started.status_code == 200, started.json
        view = client.get(base).json
        quiz = next(item["id"] for item in view["setup"]["items"] if item["title"] == "Quiz 1")
        row = next(student["row"] for student in view["students"] if student["name"] == "Example, Alice")
        current = view["cells"][quiz][row]
        typed = client.post(base + "/scores", json={"request_id": "typed-alice-1", "changes": [
            {"item_id": quiz, "row": row, "base": current["id"], "status": "scored", "points": "10"}]}, headers=HEADERS)
        assert typed.status_code == 200, typed.json
        fake.assignment(course_a, quiz_a)["published"] = False  # as Put on Canvas leaves it unless Published is ticked
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
                page.goto(f"{address}/#course/{group['id']}/section/507")
                page.locator(".page-heading .page-heading-secondary").get_by_role("button", name="Sync with Canvas", exact=True).click()
                expect(dialog.get_by_role("heading", name="Sync with Canvas")).to_be_visible(timeout=20000)
                tick = dialog.locator(f'[data-publish="{quiz}"]')
                expect(tick).to_be_checked(timeout=30000)  # ticked by default
                expect(dialog.locator(".gbd-summary")).to_contain_text("Publishes 1 assignment in Canvas first, then sends its grades.")
                # Canvas posts this Quiz 1 automatically, so the warning's title is about its grades; the assignment's line follows.
                expect(dialog.locator(".callout", has_text="Students see these grades")).to_contain_text("Students see “Quiz 1” in Canvas once it is published.")
                submit = dialog.locator('button[type="submit"]')
                expect(submit).to_contain_text("1 grade to Canvas")
                # Unticked, it stays unpublished: named under Can't sync, and nothing goes to Canvas.
                tick.uncheck()
                expect(dialog.locator(".gbd-warnings")).to_contain_text("is not published in Canvas: tick it under Publish in Canvas to send its grades")
                expect(submit).not_to_contain_text("to Canvas")
                tick.check()
                expect(submit).to_contain_text("1 grade to Canvas")
                assert not any(w[0] == "PUT" for w in fake.writes), "nothing is published before the instructor presses Sync"
                submit.click()
                expect(dialog).to_contain_text("1 assignment published in Canvas", timeout=30000)
                expect(dialog).to_contain_text("1 grade now shows in Canvas")
                assert fake.assignment(course_a, quiz_a)["published"] is True, fake.assignment(course_a, quiz_a)
                assert fake.submissions[course_a][(alice, quiz_a)]["score"] == 10.0, fake.submissions[course_a]
                dialog.get_by_role("button", name="Close", exact=True).click()  # the footer's: the header's is "Close dialog"
                expect(dialog).to_be_hidden()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Canvas publish browser check passed")


if __name__ == "__main__":
    main()
