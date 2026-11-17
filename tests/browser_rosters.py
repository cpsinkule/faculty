"""Exercise actual multipart uploads, private IDs, isolation, and the roster table at the narrowest desktop width."""
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from test_rosters import csv_bytes, xlsx_bytes, STUDENTS
from browser_course_pages import seed_course


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = app.test_client()
        seed_course(client, sections=[507, 508])
        seed_course(client, sections=[507, 508], term="Spring", is_current=False)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(address + "/#all-courses")
                page.get_by_role("link", name="Open MATH 140 · Fall 2026", exact=True).click()
                expect(page.get_by_role("tab", name="Home", exact=True)).to_have_attribute("aria-selected", "true")
                page.get_by_role("tab", name="Section 507", exact=True).click()
                page.locator("[data-course-roster-link]").click()  # a section tab shows its gradebook; the roster is a link away
                panel = page.locator("#section-roster")
                expect(panel.get_by_role("button")).to_have_count(1)
                expect(panel.get_by_role("button", name="Import roster", exact=True)).to_be_visible()
                Path("test-results").mkdir(exist_ok=True)
                page.screenshot(path="test-results/roster-empty.png", full_page=True)

                def upload(content, filename):
                    page.locator("#section-roster").get_by_role("button", name="Import roster", exact=True).click()
                    expect(page.get_by_role("dialog")).to_contain_text("Section 507")
                    page.get_by_label("Roster export", exact=True).set_input_files({"name": filename, "mimeType": "application/octet-stream", "buffer": content})
                    page.get_by_role("dialog").get_by_role("button", name="Import roster", exact=True).click()

                upload(csv_bytes(), "roster.csv")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(panel.locator("tbody tr")).to_have_count(2)
                ids = panel.locator(".private-student-id").all_text_contents()
                assert all(len(value) == 10 and value.isdigit() for value in ids), ids
                expect(panel).to_contain_text("001234567")
                # A class list: name, UIN and email first, the private ID last; columns empty for everyone are behind a toggle.
                headers = panel.get_by_role("columnheader")
                expect(headers.first).to_have_text("Name")
                expect(headers.nth(1)).to_have_text("UIN")
                expect(headers.nth(2)).to_have_text("Email")
                expect(headers.last).to_have_text("Private course ID")
                expect(panel.get_by_role("columnheader", name="Support", exact=True)).to_have_count(0)
                expect(panel.get_by_role("columnheader", name="Updated Final", exact=True)).to_have_count(0)
                panel.get_by_role("button", name="Show 3 empty columns", exact=True).click()
                expect(panel.get_by_role("columnheader", name="Updated Final", exact=True)).to_have_count(1)
                panel.get_by_role("button", name="Hide 3 empty columns", exact=True).click()
                expect(panel.get_by_role("columnheader", name="Updated Final", exact=True)).to_have_count(0)
                expect(panel.locator(".roster-summary")).to_contain_text("2 students")
                expect(panel.locator(".roster-summary")).to_contain_text("from roster.csv · 2 added · 0 updated")
                # '/' finds a student; a search keeps its rows through a background refresh.
                page.locator("#page-title").focus()
                page.keyboard.press("/")
                expect(page.get_by_label("Find a student", exact=True)).to_be_focused()
                page.get_by_label("Find a student", exact=True).fill(ids[0])
                expect(panel.locator("tbody tr:visible")).to_have_count(1)
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_timeout(600)
                expect(page.get_by_label("Find a student", exact=True)).to_have_value(ids[0])
                expect(panel.locator("tbody tr:visible")).to_have_count(1)
                page.get_by_label("Find a student", exact=True).fill("")
                # Sortable headers.
                name_header = panel.get_by_role("columnheader", name="Name", exact=True)
                expect(name_header).to_have_attribute("aria-sort", "none")
                name_header.get_by_role("button").click()
                expect(name_header).to_have_attribute("aria-sort", "ascending")
                expect(panel.locator("tbody tr").first).to_contain_text("Example, Alice")
                name_header.get_by_role("button").click()
                expect(name_header).to_have_attribute("aria-sort", "descending")
                expect(panel.locator("tbody tr").first).to_contain_text("Example, Bob")
                name_header.get_by_role("button").click()
                expect(name_header).to_have_attribute("aria-sort", "ascending")
                # Copy emails copies the rows shown.
                panel.get_by_role("button", name="Copy emails", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("Copied 2 email addresses.")
                assert page.evaluate("navigator.clipboard.readText()") == "alice@example.test, bob@example.test"
                page.get_by_label("Find a student", exact=True).fill("Alice")
                panel.get_by_role("button", name="Copy emails", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("Copied 1 email address.")
                assert page.evaluate("navigator.clipboard.readText()") == "alice@example.test"
                page.get_by_label("Find a student", exact=True).fill("")
                # A student's quick view shows everything saved for them, from the loaded roster.
                panel.get_by_role("button", name="Example, Alice", exact=True).click()
                quick = page.get_by_role("dialog")
                expect(quick.locator("#dialog-title")).to_have_text("Example, Alice")
                expect(quick).to_contain_text("MATH 140 · Fall 2026 · Section 507")
                expect(quick).to_contain_text(ids[0])
                expect(quick).to_contain_text("Degree Candidates")
                expect(quick).to_contain_text("No DR exam bookings.")
                expect(quick.get_by_role("link", name=re.compile(r"^Section \d+ gradebook$"))).to_be_visible()
                quick.get_by_role("button", name="Close", exact=True).click()
                expect(quick).not_to_be_visible()
                page.screenshot(path="test-results/roster-desktop.png", full_page=True)

                # Excel reimport updates the same IDs and escapes arbitrary exported text.
                changed = [row.copy() for row in STUDENTS]
                changed[0][4] = '<script>window.rosterInjection=true</script>'
                upload(xlsx_bytes(changed), "roster.xlsx")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(panel).to_contain_text("0 added · 2 updated")
                assert panel.locator(".private-student-id").all_text_contents() == ids
                expect(panel).to_contain_text(changed[0][4])
                assert not page.evaluate("Boolean(window.rosterInjection)")
                page.reload()
                expect(panel.locator("tbody tr")).to_have_count(2)
                assert panel.locator(".private-student-id").all_text_contents() == ids

                upload(b"Name,Email\nExample,student@example.test", "bad.csv")
                expect(page.locator("#form-error")).to_contain_text("UIN")
                expect(page.get_by_role("dialog")).to_be_visible()
                page.get_by_role("button", name="Cancel", exact=True).click()
                assert panel.locator(".private-student-id").all_text_contents() == ids

                # A Q drop on the registrar's roster (requirement, 2026-10-15) is dropped by the import itself since 2026-10-25, and
                # the roster says so.
                quitting = [row.copy() for row in STUDENTS]
                quitting[1][3], quitting[1][9] = "Q-Drop State Limited", "Q"
                upload(csv_bytes(quitting), "q-drop.csv")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(panel.locator("[data-roster-registrar-dropped]")).to_contain_text("Example, Bob · Q drop")
                expect(panel.locator("[data-roster-leaving]")).to_have_count(0)
                page.screenshot(path="test-results/roster-leaving.png", full_page=True)
                expect(panel.locator("tbody tr:visible")).to_have_count(1)

                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): at 1280px the page does not scroll sideways (a wide roster
                # scrolls inside its own box).
                page.set_viewport_size({"width": 1280, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Roster page overflow at 1280"
                page.screenshot(path="test-results/roster-1280.png", full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("tab", name="Section 508", exact=True).click()
                page.locator("[data-course-roster-link]").click()
                expect(panel).to_contain_text("Import this section’s roster")
                expect(panel.locator("tbody tr")).to_have_count(0)
                page.get_by_role("tab", name="Home", exact=True).click()
                page.locator("#course-shared").get_by_role("link", name="Spring 2026", exact=True).click()
                page.get_by_role("tab", name="Section 507", exact=True).click()
                page.locator("[data-course-roster-link]").click()
                expect(panel).to_contain_text("Import this section’s roster")
                upload(csv_bytes(), "spring.csv")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(panel.locator("tbody tr")).to_have_count(2)
                later = panel.locator(".private-student-id").all_text_contents()
                assert all(a[:5] == b[:5] and a != b for a, b in zip(ids, later))

                # The term's home: each section tile says how many students it holds (catalog counts only).
                page.get_by_role("tab", name="Home", exact=True).click()
                expect(page.locator("#section-tile-507")).to_have_text("2 students")
                expect(page.locator("#section-tile-508")).to_have_text("No roster yet · import it on the section page")
                expect(page.locator("#course-term-overview")).to_contain_text("No exams, seating charts, grading or response cards for this term yet.")

                # Import rosters: several files, each sent only with the section the instructor chose for it.
                page.get_by_role("button", name="Import rosters", exact=True).click()
                multi = page.get_by_role("dialog")
                expect(multi).to_contain_text("Nobody is removed.")
                page.get_by_label("Roster exports", exact=True).set_input_files([
                    {"name": "section-508.csv", "mimeType": "text/csv", "buffer": csv_bytes()},
                    {"name": "other.csv", "mimeType": "text/csv", "buffer": csv_bytes(STUDENTS[:1])},
                ])
                first_choice = multi.get_by_label("Section for section-508.csv", exact=True)
                second_choice = multi.get_by_label("Section for other.csv", exact=True)
                # A file whose name names a section has it chosen (requirement, 2026-10-15); another starts unchosen.
                expect(first_choice).to_have_value("508")
                expect(second_choice).to_have_value("")
                first_choice.select_option("")
                multi.get_by_role("button", name="Import rosters", exact=True).click()
                expect(page.locator("#form-error")).to_have_text("Choose a section for at least one file.")
                first_choice.select_option("508")
                second_choice.select_option("508")
                multi.get_by_role("button", name="Import rosters", exact=True).click()
                expect(page.locator("#form-error")).to_contain_text("Section 508 is chosen for more than one file.")
                second_choice.select_option("")
                multi.get_by_role("button", name="Import rosters", exact=True).click()
                results = multi.locator("[data-roster-result]")
                expect(results.first).to_have_text("Section 508: 2 students · 2 added · 0 updated.")
                expect(multi.locator("[data-roster-leaving-link]")).to_have_count(0)
                expect(results.nth(1)).to_have_text("Not sent: no section chosen.")
                multi.get_by_role("button", name="Close", exact=True).click()
                expect(page.locator("#section-tile-508")).to_have_text("2 students")
                # A file that leaves a student out says so in its result, with the way to the roster's Drop (requirement, 2026-10-15).
                page.get_by_role("button", name="Import rosters", exact=True).click()
                multi = page.get_by_role("dialog")
                page.get_by_label("Roster exports", exact=True).set_input_files([{"name": "MATH-140-507_202631.csv", "mimeType": "text/csv", "buffer": csv_bytes(STUDENTS[:1])}])
                expect(multi.get_by_label("Section for MATH-140-507_202631.csv", exact=True)).to_have_value("507")
                multi.get_by_role("button", name="Import rosters", exact=True).click()
                expect(multi.locator("[data-roster-result]").first).to_contain_text("Leaving: Example, Bob (Not on the roster imported")
                page.screenshot(path="test-results/roster-multi-leaving.png", full_page=True)
                multi.get_by_role("link", name="Review on the roster", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(panel.locator("[data-roster-leaving]")).to_contain_text("Example, Bob")
                page.get_by_role("tab", name="Home", exact=True).click()

                # Start next term: Add a course, with this course's code, name and sections; term and year left to choose.
                page.get_by_role("button", name="Start next term", exact=True).click()
                course_form = page.locator("#course-form")
                expect(page.locator("#dialog-title")).to_have_text("Add a course")
                expect(page.get_by_label("Course name", exact=True)).to_have_value("Calculus I")
                expect(page.locator("#course-code-preview")).to_have_text("MATH 140")
                expect(page.get_by_label("Multiple Sections?", exact=True)).to_be_checked()
                expect(course_form.locator('[name="section"]')).to_have_count(2)
                expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("507")
                expect(page.get_by_role("spinbutton", name="Section 2", exact=True)).to_have_value("508")
                expect(page.get_by_role("combobox", name="Term", exact=True)).to_have_value("")
                expect(page.get_by_role("spinbutton", name="Year", exact=True)).to_have_value("")
                page.get_by_role("button", name="Cancel", exact=True).click()

                # The course list shows each section's student count.
                page.goto(address + "/#all-courses")
                page.locator("details.course-other-terms summary").click()
                expect(page.get_by_role("link", name="Open MATH 140 Section 508, 2 students", exact=True)).to_be_visible()
                # Fall's 507 counts its one current student: Bob was dropped after his Q drop.
                expect(page.get_by_role("link", name="Open MATH 140 Section 507, 2 students", exact=True)).to_have_count(1)
                expect(page.get_by_role("link", name="Open MATH 140 Section 507, 1 student", exact=True)).to_have_count(1)
                assert not errors, errors
                browser.close()
                print("Roster browser checks passed: CSV/XLSX upload, all columns, private IDs, reimports, reload, search, errors, escaping, term/section isolation, 1280px desktop layout.")
        finally:
            server.close()


if __name__ == "__main__":
    main()
