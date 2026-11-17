"""Student notes in a real browser (AGENTS.md "Student notes"): the roster's student window lists a student's notes newest day first,
adds one in place (today by default), edits one with Ctrl+Enter and deletes one with Undo; the gradebook's record shows the same
notes and adds one without its own form submitting; the palette's "Write a note about a student" picks the student first. Notes are
shown as text. Checked at 1440px and at the narrowest desktop width (1280px). Synthetic course and roster only."""
import io
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from app import create_app  # noqa: E402
from browser_course_pages import seed_course  # noqa: E402
from rosters import connect  # noqa: E402
from test_rosters import csv_bytes  # noqa: E402
from test_storage import HEADERS  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = app.test_client()
        client.get("/").close()
        seed_course(client, sections=[507, 508])
        group = client.get("/api/state").json["course_groups"][0]["id"]
        for number in (507, 508):
            answer = client.post(f"/api/course-groups/{group}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        with connect(app.config["DATABASE"]) as db:
            term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
            enrollments = {r["uin"]: r["id"] for r in db.execute("SELECT id, uin FROM student_enrollments")}
        alice, bob = enrollments["001234567"], enrollments["123456789"]
        saved = client.post(f"/api/gradebook/terms/{term}/setup", headers=HEADERS, json={"base_revision": None, "request_id": "setup",
                            "setup": {"items": [{"title": "Quiz 3", "points": "10", "due_date": "2026-09-17"}]}})
        assert saved.status_code == 200, saved.json
        seeded = client.post("/api/student-notes", headers=HEADERS, json={"enrollment_id": alice, "date": "2026-09-29", "request_id": "seed",
                                                                         "text": "Came to office hours about the chain rule."})
        assert seeded.status_code == 201, seeded.json

        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(f"{address}/#course/{group}/section/507/roster")
                page.get_by_role("button", name="Example, Alice", exact=True).click()
                quick = page.locator("#dialog")
                expect(quick.get_by_role("heading", name="Notes")).to_be_visible(timeout=20000)
                part = quick.locator("[data-student-notes]")
                expect(part.locator(".student-note")).to_have_count(1)
                expect(part.locator(".student-note").first).to_contain_text("Tue, Sep 29")

                # Add one in place: today by default, the words kept as text.
                today = page.evaluate("(() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; })()")
                part.get_by_role("button", name="Add a note…").click()
                editor = part.locator("[data-note-editor='new']")
                expect(editor.get_by_label("Note")).to_be_focused()
                expect(editor.get_by_label("Date")).to_have_value(today)
                editor.get_by_label("Note").fill("Scheduled one-on-one office hours today and didn't show.\n<b>Emailed them</b>")
                editor.get_by_role("button", name="Save note").click()
                expect(page.locator("#toast")).to_contain_text("Note saved")
                notes = part.locator(".student-note")
                expect(notes).to_have_count(2)
                expect(notes.first.locator(".student-note-text")).to_have_text("Scheduled one-on-one office hours today and didn't show.\n<b>Emailed them</b>")
                expect(part.get_by_role("button", name="Add a note…")).to_be_focused()

                # Edit the older one; Ctrl+Enter saves.
                part.get_by_role("button", name="Edit the note of Tue, Sep 29").click()
                text = part.locator("[data-note-editor]").get_by_label("Note")
                expect(text).to_have_value("Came to office hours about the chain rule.")
                text.fill("Came to office hours about the chain rule; well prepared.")
                text.press("Control+Enter")
                expect(page.locator("#toast")).to_contain_text("Note changed")
                expect(notes.nth(1)).to_contain_text("well prepared")
                expect(notes.nth(1).locator(".student-note-edited")).to_have_text("edited")

                # Delete with Undo, offered in the card (the window is modal: a toast's button outside it cannot be clicked).
                notes.nth(1).get_by_role("button", name="Delete the note of Tue, Sep 29").click()
                expect(notes).to_have_count(1)
                expect(part.locator(".student-note-undo")).to_contain_text("Note of Tue, Sep 29 deleted.")
                expect(part.get_by_role("button", name="Undo")).to_be_focused()
                part.get_by_role("button", name="Undo").click()
                expect(page.locator("#toast")).to_contain_text("Note restored")
                expect(notes).to_have_count(2)
                expect(part.locator(".student-note-undo")).to_have_count(0)

                # Words typed and not saved outlive the window (Escape here): the card opens its editor with them again.
                part.get_by_role("button", name="Add a note…").click()
                part.locator("[data-note-editor='new']").get_by_label("Note").fill("Half-written: asked about the")
                page.keyboard.press("Escape")
                expect(quick).not_to_be_visible()
                page.get_by_role("button", name="Example, Alice", exact=True).click()
                kept = quick.locator("[data-student-notes] [data-note-editor='new']")
                expect(kept.get_by_label("Note")).to_have_value("Half-written: asked about the", timeout=20000)
                expect(kept.locator("[data-note-kept]")).to_have_text("Your unsaved note was kept.")
                kept.get_by_role("button", name="Cancel").click()
                expect(part.get_by_role("button", name="Add a note…")).to_be_visible()
                # Nothing left to keep: a new note's words typed and removed again, then an edit put back to the saved words.
                part.get_by_role("button", name="Add a note…").click()
                blank = part.locator("[data-note-editor='new']").get_by_label("Note")
                blank.press_sequentially("x")
                blank.press("Backspace")
                part.get_by_role("button", name="Edit the note of Tue, Sep 29").click()
                same = part.locator("[data-note-editor]").get_by_label("Note")
                expect(same).to_have_value("Came to office hours about the chain rule; well prepared.")
                same.press("Control+End")
                same.press_sequentially("!")
                same.press("Backspace")
                page.keyboard.press("Escape")
                expect(quick).not_to_be_visible()
                page.get_by_role("button", name="Example, Alice", exact=True).click()
                expect(part.locator(".student-note")).to_have_count(2, timeout=20000)
                expect(part.get_by_role("button", name="Add a note…")).to_be_visible()
                expect(part.locator("[data-note-editor]")).to_have_count(0)
                expect(part.locator("[data-note-kept]")).to_have_count(0)
                quick.get_by_role("button", name="Close", exact=True).last.click()

                # The gradebook's record shows the same notes; Enter in the date box saves the note, not the record's own form.
                page.goto(f"{address}/#course/{group}/section/507")
                page.locator("[data-student]").filter(has_text="Example, Alice").first.click()
                card = page.locator("#gradebook-student-form")
                record = card.locator("[data-student-notes]")
                expect(record.locator(".student-note")).to_have_count(2, timeout=20000)
                record.get_by_role("button", name="Add a note…").click()
                record.get_by_label("Note", exact=True).fill("Asked about the makeup policy.")
                record.get_by_label("Date", exact=True).fill("2026-09-30")
                record.get_by_label("Date", exact=True).press("Enter")
                expect(record.locator(".student-note")).to_have_count(3)
                expect(card).to_be_visible()
                expect(record.locator(".student-note").nth(1)).to_contain_text("Wed, Sep 30")
                card.get_by_role("button", name="Close", exact=True).click()

                # The palette (from Home: closing the record returns focus to the grid, which keeps its own keys): choose the
                # student, then write.
                page.goto(f"{address}/#overview")
                expect(page.locator("#dialog")).not_to_be_visible()
                page.locator("main").click(position={"x": 5, "y": 5})
                page.keyboard.press("Control+k")
                expect(page.locator("[data-palette-input]")).to_be_visible()
                page.locator("[data-palette-input]").fill("write a note")
                page.keyboard.press("Enter")
                form = page.locator("#student-note-form")
                expect(form.get_by_role("heading", name="Write a note about a student")).to_be_visible()
                form.locator("[data-picker-text]").fill("bob")
                page.get_by_role("option", name="Example, Bob · section 507, 508").click()
                form.get_by_label("Note").fill("Missed the review session.")
                form.get_by_role("button", name="Save note").click()
                expect(page.locator("#toast")).to_contain_text("Note saved")
                expect(form).to_be_hidden()
                assert [n["text"] for n in client.get(f"/api/student-notes/students/{bob}").json["notes"]] == ["Missed the review session."]

                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): the narrowest desktop window, in the one appearance.
                narrow = browser.new_page(viewport={"width": 1280, "height": 900})
                narrow.on("pageerror", lambda error: errors.append(str(error)))
                narrow.goto(f"{address}/#course/{group}/section/507/roster")
                narrow.get_by_role("button", name="Example, Alice", exact=True).click()
                narrow_part = narrow.locator("#dialog [data-student-notes]")
                expect(narrow_part.locator(".student-note")).to_have_count(3, timeout=20000)
                narrow_part.get_by_role("button", name="Add a note…").click()
                width = narrow.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert width <= 0, f"the page scrolls sideways by {width}px at 1280px"
                inner = narrow.evaluate("(() => { const d = document.getElementById('dialog'); return d.scrollWidth - d.clientWidth; })()")
                assert inner <= 0, f"the student window scrolls sideways by {inner}px"
                Path("test-results").mkdir(exist_ok=True)
                narrow.screenshot(path="test-results/student-notes-1280.png")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Student notes browser checks passed: roster window add/edit/delete+undo, gradebook record, palette and the 1280px desktop width.")


if __name__ == "__main__":
    main()
